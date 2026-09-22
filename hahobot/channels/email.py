"""Email channel implementation using IMAP polling + SMTP replies."""

import asyncio
import html
import imaplib
import mimetypes
import re
import smtplib
import ssl
import threading
from datetime import date
from email import policy
from email.errors import UndecodableBytesDefect
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from loguru import logger

from hahobot.bus.events import OutboundMessage
from hahobot.bus.queue import MessageBus
from hahobot.channels.base import BaseChannel
from hahobot.config.paths import get_media_dir
from hahobot.config.schema import EmailConfig, EmailInstanceConfig
from hahobot.utils.helpers import safe_filename


class EmailChannel(BaseChannel):
    """
    Email channel.

    Inbound:
    - Poll IMAP mailbox for unread messages.
    - Convert each message into an inbound event.

    Outbound:
    - Send responses via SMTP back to the sender address.
    """

    name = "email"
    display_name = "Email"
    _IMAP_MONTHS = (
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    )
    _IMAP_RECONNECT_MARKERS = (
        "disconnected for inactivity",
        "eof occurred in violation of protocol",
        "socket error",
        "connection reset",
        "broken pipe",
        "bye",
    )
    _IMAP_MISSING_MAILBOX_MARKERS = (
        "mailbox doesn't exist",
        "select failed",
        "no such mailbox",
        "can't open mailbox",
        "does not exist",
    )
    _IMAP_TIMEOUT_SECONDS = 30.0
    _IMAP_STOP_TIMEOUT_SECONDS = 35.0

    @classmethod
    def default_config(cls) -> dict[str, object]:
        return EmailConfig().model_dump(by_alias=True)

    def __init__(self, config: EmailConfig | EmailInstanceConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: EmailConfig | EmailInstanceConfig = config
        self._self_addresses = self._collect_self_addresses()
        self._last_subject_by_chat: dict[str, str] = {}
        self._last_message_id_by_chat: dict[str, str] = {}
        self._processed_uids: set[str] = set()  # Capped to prevent unbounded growth
        self._selected_mailbox: str | None = None
        self._imap_uid_validity: tuple[str, str] | None = None
        self._poll_stop_event: threading.Event | None = None
        self._stop_wakeup = asyncio.Event()
        self._lifecycle_lock = asyncio.Lock()
        self._start_task: asyncio.Task[None] | None = None
        self._MAX_PROCESSED_UIDS = 100000

    @staticmethod
    async def _run_blocking(func, /, *args, **kwargs):
        """Run blocking IMAP/SMTP work in a thread to avoid blocking the event loop.

        Earlier versions ran these synchronously for reliability in certain
        deployment/test environments; the threadpool path is now preferred to
        keep the event loop responsive during slow network operations.
        """
        return await asyncio.to_thread(func, *args, **kwargs)

    async def start(self) -> None:
        """Start polling IMAP for inbound emails."""
        if not self.config.consent_granted:
            logger.warning(
                "Email channel disabled: consent_granted is false. "
                "Set channels.email.consentGranted=true after explicit user permission."
            )
            return

        if not self._validate_config():
            return

        if self._running:
            return

        async with self._lifecycle_lock:
            if self._running:
                return
            owner_task = asyncio.current_task()
            stop_event = threading.Event()
            self._start_task = owner_task
            self._poll_stop_event = stop_event
            self._stop_wakeup.clear()
            self._running = True
            try:
                if not self.config.verify_dkim and not self.config.verify_spf:
                    logger.warning(
                        "Email channel: DKIM and SPF verification are both DISABLED. "
                        "Messages will be filtered only by their From header and allowFrom."
                    )
                logger.info("Starting Email channel (IMAP polling mode)...")

                poll_seconds = max(5, int(self.config.poll_interval_seconds))
                while self._running and not stop_event.is_set():
                    try:
                        poll_task = asyncio.create_task(
                            self._run_blocking(
                                self._fetch_new_messages,
                                stop_event,
                            )
                        )
                        try:
                            inbound_items = await asyncio.shield(poll_task)
                        except asyncio.CancelledError:
                            # Keep ownership of the underlying to_thread call
                            # until it observes this run's stop flag. This
                            # prevents an immediate restart from overlapping an
                            # orphaned poll and drains any already-committed UID.
                            stop_event.set()
                            try:
                                inbound_items = await poll_task
                                await self._publish_committed_items(inbound_items, stop_event)
                            except Exception as exc:
                                logger.error("Email polling cleanup error: {}", exc)
                            raise
                        # A returned item has already entered UID dedupe and may
                        # be marked Seen. Drain that committed batch into the
                        # bus even when stop raced with the worker's return.
                        await self._publish_committed_items(inbound_items, stop_event)
                    except Exception as e:
                        logger.error("Email polling error: {}", e)

                    if not self._running or stop_event.is_set():
                        break
                    try:
                        await asyncio.wait_for(self._stop_wakeup.wait(), timeout=poll_seconds)
                    except TimeoutError:
                        pass
            finally:
                stop_event.set()
                self._running = False
                if self._poll_stop_event is stop_event:
                    self._poll_stop_event = None
                if self._start_task is owner_task:
                    self._start_task = None

    async def _publish_committed_items(
        self,
        inbound_items: list[dict[str, Any]],
        stop_event: threading.Event,
    ) -> None:
        """Drain a locally committed worker batch even if the owner is cancelled."""
        if not inbound_items:
            return

        publish_task = asyncio.create_task(self._publish_inbound_items(inbound_items))
        try:
            await asyncio.shield(publish_task)
        except asyncio.CancelledError:
            # The worker has already entered these UIDs into the local dedupe
            # set and may have marked them Seen. Cancellation must therefore
            # stop future polling but cannot abandon this committed batch.
            stop_event.set()
            try:
                await publish_task
            except Exception as exc:
                logger.error("Email committed-batch delivery error during shutdown: {}", exc)
            raise

    async def _publish_inbound_items(self, inbound_items: list[dict[str, Any]]) -> None:
        """Publish a worker batch whose UIDs are already committed locally."""
        for item in inbound_items:
            sender = item["sender"]
            subject = item.get("subject", "")
            message_id = item.get("message_id", "")

            if subject:
                self._last_subject_by_chat[sender] = subject
            if message_id:
                self._last_message_id_by_chat[sender] = message_id

            await self._handle_message(
                sender_id=sender,
                chat_id=sender,
                content=item["content"],
                media=item.get("media") or None,
                metadata=item.get("metadata", {}),
            )

    async def stop(self) -> None:
        """Stop polling loop."""
        self._running = False
        if self._poll_stop_event is not None:
            self._poll_stop_event.set()
        self._stop_wakeup.set()

        owner_task = self._start_task
        if owner_task is None or owner_task is asyncio.current_task() or owner_task.done():
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(owner_task),
                timeout=self._IMAP_STOP_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "Email polling did not stop within {:.0f}s; waiting for bounded socket I/O",
                self._IMAP_STOP_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            if not owner_task.cancelled():
                raise

    async def send(self, msg: OutboundMessage) -> None:
        """Send email via SMTP."""
        # Email is a non-streaming channel: never emit a message per progress/tool-hint
        # update, which would otherwise send a near-empty email after each tool call.
        # Every other channel guards _progress in its send path; mirror that here.
        # Ported from nanobot cbf1ede.
        if (msg.metadata or {}).get("_progress"):
            logger.debug("Skip progress message to {}", msg.chat_id)
            return

        if not self.config.consent_granted:
            logger.warning("Skip email send: consent_granted is false")
            return

        if not self.config.smtp_host:
            logger.warning("Email channel SMTP host not configured")
            return

        to_addr = msg.chat_id.strip()
        if not to_addr:
            logger.warning("Email channel missing recipient address")
            return

        # Determine if this is a reply (recipient has sent us an email before)
        is_reply = to_addr in self._last_subject_by_chat
        force_send = bool((msg.metadata or {}).get("force_send"))

        # autoReplyEnabled only controls automatic replies, not proactive sends
        if is_reply and not self.config.auto_reply_enabled and not force_send:
            logger.info("Skip automatic email reply to {}: auto_reply_enabled is false", to_addr)
            return

        base_subject = self._last_subject_by_chat.get(to_addr, "hahobot reply")
        subject = self._reply_subject(base_subject)
        if msg.metadata and isinstance(msg.metadata.get("subject"), str):
            override = msg.metadata["subject"].strip()
            if override:
                subject = override

        in_reply_to = self._last_message_id_by_chat.get(to_addr)

        try:
            await self._run_blocking(
                self._smtp_send_message,
                to_addr=to_addr,
                subject=subject,
                content=msg.content or "",
                in_reply_to=in_reply_to,
                media=list(msg.media or []),
            )
        except Exception as e:
            logger.error("Error sending email to {}: {}", to_addr, e)
            raise

    def _validate_config(self) -> bool:
        missing = []
        if not self.config.imap_host:
            missing.append("imap_host")
        if not self.config.imap_username:
            missing.append("imap_username")
        if not self.config.imap_password:
            missing.append("imap_password")
        if not self.config.smtp_host:
            missing.append("smtp_host")
        if not self.config.smtp_username:
            missing.append("smtp_username")
        if not self.config.smtp_password:
            missing.append("smtp_password")

        if missing:
            logger.error("Email channel not configured, missing: {}", ", ".join(missing))
            return False
        return True

    def _smtp_send_message(
        self,
        *,
        to_addr: str,
        subject: str,
        content: str,
        in_reply_to: str | None = None,
        media: list[str] | None = None,
    ) -> None:
        """Build and send one outbound email inside the worker thread."""
        msg = EmailMessage()
        msg["From"] = (
            self.config.from_address or self.config.smtp_username or self.config.imap_username
        )
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.set_content(content)
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to
        self._attach_media(msg, media or [])
        self._smtp_send(msg)

    def _attach_media(self, msg: EmailMessage, media: list[str]) -> None:
        """Attach agent-delivered files to an outbound email, bounded by config.

        Missing or oversized files are skipped with a warning rather than failing the
        whole send; the message body still goes out. Reuses the inbound attachment
        bounds (`max_attachments_per_email`, `max_attachment_size`).
        """
        attached = 0
        for raw_path in media:
            if attached >= self.config.max_attachments_per_email:
                logger.warning(
                    "Email outbound media: reached max_attachments_per_email ({}), skipping rest",
                    self.config.max_attachments_per_email,
                )
                break
            path = Path(str(raw_path)).expanduser()
            if not path.is_file():
                logger.warning("Email outbound media: file not found, skipping: {}", raw_path)
                continue
            try:
                size = path.stat().st_size
            except OSError as exc:
                logger.warning("Email outbound media: cannot stat {}: {}", raw_path, exc)
                continue
            if size > self.config.max_attachment_size:
                logger.warning(
                    "Email outbound media: {} is {} bytes, exceeds max_attachment_size ({}), skipping",
                    path.name,
                    size,
                    self.config.max_attachment_size,
                )
                continue
            try:
                data = path.read_bytes()
            except OSError as exc:
                logger.warning("Email outbound media: cannot read {}: {}", raw_path, exc)
                continue
            mime, _ = mimetypes.guess_type(path.name)
            maintype, _, subtype = (mime or "application/octet-stream").partition("/")
            if not subtype:
                maintype, subtype = "application", "octet-stream"
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)
            attached += 1

    def _smtp_send(self, msg: EmailMessage) -> None:
        timeout = 30
        if self.config.smtp_use_ssl:
            with smtplib.SMTP_SSL(
                self.config.smtp_host,
                self.config.smtp_port,
                timeout=timeout,
            ) as smtp:
                smtp.login(self.config.smtp_username, self.config.smtp_password)
                smtp.send_message(msg)
            return

        with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=timeout) as smtp:
            if self.config.smtp_use_tls:
                smtp.starttls(context=ssl.create_default_context())
            smtp.login(self.config.smtp_username, self.config.smtp_password)
            smtp.send_message(msg)

    def _fetch_new_messages(
        self,
        stop_event: threading.Event | None = None,
    ) -> list[dict[str, Any]]:
        """Poll IMAP and return parsed unread messages."""
        return self._fetch_messages(
            search_criteria=("UNSEEN",),
            mark_seen=self.config.mark_seen,
            dedupe=True,
            limit=0,
            stop_event=stop_event,
        )

    def fetch_messages_between_dates(
        self,
        start_date: date,
        end_date: date,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Fetch messages in [start_date, end_date) by IMAP date search.

        This is used for historical summarization tasks (e.g. "yesterday").
        """
        if end_date <= start_date:
            return []

        return self._fetch_messages(
            search_criteria=(
                "SINCE",
                self._format_imap_date(start_date),
                "BEFORE",
                self._format_imap_date(end_date),
            ),
            mark_seen=False,
            dedupe=False,
            limit=max(1, int(limit)),
        )

    def _fetch_messages(
        self,
        search_criteria: tuple[str, ...],
        mark_seen: bool,
        dedupe: bool,
        limit: int,
        stop_event: threading.Event | None = None,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        cycle_uids: set[str] = set()

        for attempt in range(2):
            if stop_event is not None and stop_event.is_set():
                return messages
            try:
                self._fetch_messages_once(
                    search_criteria,
                    mark_seen,
                    dedupe,
                    limit,
                    messages,
                    cycle_uids,
                    stop_event,
                )
                return messages
            except Exception as exc:
                if attempt == 1 or not self._is_stale_imap_error(exc):
                    raise
                logger.warning("Email IMAP connection went stale, retrying once: {}", exc)

        return messages

    def _fetch_messages_once(
        self,
        search_criteria: tuple[str, ...],
        mark_seen: bool,
        dedupe: bool,
        limit: int,
        messages: list[dict[str, Any]],
        cycle_uids: set[str],
        stop_event: threading.Event | None,
    ) -> None:
        """Fetch messages by arbitrary IMAP search criteria.

        Resolve stable UIDs before fetching message data, then fetch only headers
        until sender, authentication, and allowlist checks pass.  This prevents
        rejected mail from downloading bodies or writing attachments as a side
        effect of polling.
        """
        mailbox = self.config.imap_mailbox or "INBOX"

        if stop_event is not None and stop_event.is_set():
            return

        if self.config.imap_use_ssl:
            client = imaplib.IMAP4_SSL(
                self.config.imap_host,
                self.config.imap_port,
                timeout=self._IMAP_TIMEOUT_SECONDS,
            )
        else:
            client = imaplib.IMAP4(
                self.config.imap_host,
                self.config.imap_port,
                timeout=self._IMAP_TIMEOUT_SECONDS,
            )

        try:
            client.login(self.config.imap_username, self.config.imap_password)
            if stop_event is not None and stop_event.is_set():
                return
            try:
                status, _ = client.select(mailbox)
            except Exception as exc:
                if self._is_missing_mailbox_error(exc):
                    logger.warning(
                        "Email mailbox unavailable, skipping poll for {}: {}", mailbox, exc
                    )
                    return
                raise
            if status != "OK":
                logger.warning(
                    "Email mailbox select returned {}, skipping poll for {}", status, mailbox
                )
                return
            if stop_event is not None and stop_event.is_set():
                return
            self._refresh_uid_validity(client, mailbox, cycle_uids)

            status, data = client.uid("SEARCH", None, *search_criteria)
            if stop_event is not None and stop_event.is_set():
                return
            if status != "OK" or not data or not data[0]:
                return

            uids = [raw.decode("ascii", errors="ignore") for raw in data[0].split()]
            if limit > 0 and len(uids) > limit:
                uids = uids[-limit:]

            uid_store_supported: bool | None = None
            for uid in uids:
                if stop_event is not None and stop_event.is_set():
                    return
                if not uid or uid in cycle_uids:
                    continue
                if dedupe and uid in self._processed_uids:
                    continue

                status, fetched = client.uid("FETCH", uid, "(BODY.PEEK[HEADER])")
                if stop_event is not None and stop_event.is_set():
                    return
                if status != "OK" or not fetched:
                    continue

                header_bytes = self._extract_message_bytes(fetched)
                if header_bytes is None:
                    continue

                parsed = BytesParser(policy=policy.default).parsebytes(header_bytes)
                sender = self._parse_from_address(parsed)
                if not sender:
                    self._remember_processed_uid(uid, dedupe, cycle_uids)
                    continue
                if self._is_self_address(sender):
                    logger.info("Email from {} ignored: matches bot-owned address", sender)
                    self._remember_processed_uid(uid, dedupe, cycle_uids)
                    if mark_seen:
                        uid_store_supported = self._mark_seen_uid(client, uid, uid_store_supported)
                    continue

                # Treat the receiving service's nearest Authentication-Results
                # header as a mailbox policy signal, not as local cryptographic
                # verification.  See _check_authentication_results.
                spf_pass, dkim_pass = self._check_authentication_results(parsed)
                if self.config.verify_spf and not spf_pass:
                    logger.warning(
                        "Email from {} rejected: SPF verification failed "
                        "(no 'spf=pass' in Authentication-Results header)",
                        sender,
                    )
                    self._remember_processed_uid(uid, dedupe, cycle_uids)
                    continue
                if self.config.verify_dkim and not dkim_pass:
                    logger.warning(
                        "Email from {} rejected: DKIM verification failed "
                        "(no 'dkim=pass' in Authentication-Results header)",
                        sender,
                    )
                    self._remember_processed_uid(uid, dedupe, cycle_uids)
                    continue

                if not self.is_allowed(sender):
                    self._remember_processed_uid(uid, dedupe, cycle_uids)
                    if mark_seen:
                        uid_store_supported = self._mark_seen_uid(client, uid, uid_store_supported)
                    continue

                # Only accepted messages may download a full body or attachments.
                if stop_event is not None and stop_event.is_set():
                    return
                status, full_fetched = client.uid("FETCH", uid, "(BODY.PEEK[])")
                if stop_event is not None and stop_event.is_set():
                    return
                if status != "OK" or not full_fetched:
                    continue
                raw_bytes = self._extract_message_bytes(full_fetched)
                if raw_bytes is None:
                    continue
                parsed = BytesParser(policy=policy.default).parsebytes(raw_bytes)

                subject = self._decode_header_value(parsed.get("Subject", ""))
                date_value = parsed.get("Date", "")
                message_id = parsed.get("Message-ID", "").strip()
                body = self._extract_text_body(parsed)

                if not body:
                    body = "(empty email body)"

                body = body[: self.config.max_body_chars]
                content = (
                    f"[EMAIL-CONTEXT] Email received.\n"
                    f"From: {sender}\n"
                    f"Subject: {subject}\n"
                    f"Date: {date_value}\n\n"
                    f"{body}"
                )

                # --- Attachment extraction ---
                attachment_paths: list[str] = []
                if self.config.allowed_attachment_types:
                    saved = self._extract_attachments(
                        parsed,
                        uid or "noid",
                        allowed_types=self.config.allowed_attachment_types,
                        max_size=self.config.max_attachment_size,
                        max_count=self.config.max_attachments_per_email,
                    )
                    for p in saved:
                        attachment_paths.append(str(p))
                        content += f"\n[attachment: {p.name} — saved to {p}]"

                metadata = {
                    "message_id": message_id,
                    "subject": subject,
                    "date": date_value,
                    "sender_email": sender,
                    "uid": uid,
                }
                messages.append(
                    {
                        "sender": sender,
                        "subject": subject,
                        "message_id": message_id,
                        "content": content,
                        "metadata": metadata,
                        "media": attachment_paths,
                    }
                )

                self._remember_processed_uid(uid, dedupe, cycle_uids)

                if mark_seen:
                    uid_store_supported = self._mark_seen_uid(client, uid, uid_store_supported)
        finally:
            try:
                if stop_event is not None and stop_event.is_set():
                    client.shutdown()
                else:
                    client.logout()
            except Exception:
                pass

    @classmethod
    def _is_stale_imap_error(cls, exc: Exception) -> bool:
        message = str(exc).lower()
        return any(marker in message for marker in cls._IMAP_RECONNECT_MARKERS)

    @classmethod
    def _is_missing_mailbox_error(cls, exc: Exception) -> bool:
        message = str(exc).lower()
        return any(marker in message for marker in cls._IMAP_MISSING_MAILBOX_MARKERS)

    @classmethod
    def _format_imap_date(cls, value: date) -> str:
        """Format date for IMAP search (always English month abbreviations)."""
        month = cls._IMAP_MONTHS[value.month - 1]
        return f"{value.day:02d}-{month}-{value.year}"

    def _collect_self_addresses(self) -> set[str]:
        """Return normalized addresses owned by this channel instance."""
        candidates = (
            self.config.from_address,
            self.config.smtp_username,
            self.config.imap_username,
        )
        return {
            address for candidate in candidates if (address := self._normalize_address(candidate))
        }

    @staticmethod
    def _normalize_address(value: str) -> str:
        raw = (value or "").strip()
        if not raw:
            return ""
        parsed = parseaddr(raw)[1].strip().lower()
        if parsed:
            return parsed
        if "@" in raw:
            return raw.lower()
        return ""

    def _is_self_address(self, sender: str) -> bool:
        normalized = self._normalize_address(sender)
        return bool(normalized) and normalized in self._self_addresses

    @classmethod
    def _parse_from_address(cls, message: Any) -> str:
        """Return one structurally valid From addr-spec, or fail closed."""
        get_all = getattr(message, "get_all", None)
        if callable(get_all):
            from_fields = get_all("From", [])
            if len(from_fields) != 1:
                return ""
            header = from_fields[0]
        else:
            header = message.get("From")
        addresses = getattr(header, "addresses", None)
        if addresses is not None:
            groups = getattr(header, "groups", ())
            if (
                any(
                    not isinstance(defect, UndecodableBytesDefect)
                    for defect in getattr(header, "defects", ())
                )
                or len(addresses) != 1
                or any(getattr(group, "display_name", None) is not None for group in groups)
            ):
                return ""
            address = addresses[0]
            local = str(getattr(address, "username", "") or "").strip()
            raw_domain = str(getattr(address, "domain", "") or "").strip()
            if not local or not raw_domain or raw_domain.startswith("["):
                return ""
            domain = cls._authentication_domain(raw_domain)
            if not domain:
                return ""
            addr_spec = str(getattr(address, "addr_spec", "") or "").strip().lower()
            if "@" not in addr_spec:
                return ""
            addr_local = addr_spec.rsplit("@", 1)[0]
            return f"{addr_local}@{domain}"

        candidate = parseaddr(str(header or ""))[1].strip().lower()
        if candidate.count("@") != 1:
            return ""
        local, raw_domain = candidate.rsplit("@", 1)
        domain = cls._authentication_domain(raw_domain)
        if not local or not domain or any(char.isspace() for char in local):
            return ""
        return f"{local}@{domain}"

    def _remember_processed_uid(self, uid: str, dedupe: bool, cycle_uids: set[str]) -> None:
        """Remember accepted and rejected UIDs without unbounded growth."""
        if not uid:
            return
        cycle_uids.add(uid)
        if not dedupe:
            return
        self._processed_uids.add(uid)
        if len(self._processed_uids) > self._MAX_PROCESSED_UIDS:
            self._processed_uids = set(list(self._processed_uids)[len(self._processed_uids) // 2 :])

    @staticmethod
    def _read_uid_validity(client: Any) -> str | None:
        """Read the UIDVALIDITY cached by imaplib after SELECT, when available."""
        response = getattr(client, "response", None)
        if not callable(response):
            return None
        try:
            _, data = response("UIDVALIDITY")
        except Exception:
            return None
        items = [data] if isinstance(data, (bytes, str)) else data or []
        for item in items:
            if isinstance(item, bytes):
                item = item.decode("ascii", errors="ignore")
            match = re.search(r"\d+", str(item))
            if match is not None:
                return match.group(0)
        return None

    def _refresh_uid_validity(
        self,
        client: Any,
        mailbox: str,
        cycle_uids: set[str],
    ) -> None:
        """Reset process-local UID dedupe when the mailbox UID namespace changes."""
        if self._selected_mailbox is not None and self._selected_mailbox != mailbox:
            self._processed_uids.clear()
            cycle_uids.clear()
            self._imap_uid_validity = None
        self._selected_mailbox = mailbox

        validity = self._read_uid_validity(client)
        if validity is None:
            return
        current = (mailbox, validity)
        namespace_became_known = self._imap_uid_validity is None and bool(self._processed_uids)
        namespace_changed = (
            self._imap_uid_validity is not None and self._imap_uid_validity != current
        )
        if namespace_became_known or namespace_changed:
            logger.warning(
                "Email UIDVALIDITY namespace changed for mailbox {}; "
                "clearing process-local UID dedupe",
                mailbox,
            )
            self._processed_uids.clear()
            cycle_uids.clear()
        self._imap_uid_validity = current

    @staticmethod
    def _lookup_imap_id_by_uid(client: Any, uid: str) -> bytes | None:
        """Resolve a session-local sequence number when UID STORE is unavailable."""
        status, data = client.search(None, "UID", uid)
        if status != "OK" or not data or not data[0]:
            return None
        return data[0].split()[0]

    def _mark_seen_uid(
        self,
        client: Any,
        uid: str,
        uid_store_supported: bool | None,
    ) -> bool:
        """Mark a UID as seen, falling back to sequence STORE when necessary.

        The return value records whether UID STORE works for this IMAP session so
        later messages avoid repeating a known-unsupported command.
        """
        if uid_store_supported is not False:
            try:
                status, _ = client.uid("STORE", uid, "+FLAGS", "(\\Seen)")
            except Exception as exc:
                # imaplib raises IMAP4.error for BAD commands instead of
                # returning a status.  Marking seen is best effort: never let
                # it discard an accepted message that is already queued for
                # delivery.
                logger.debug(
                    "Email UID STORE failed for UID {} ({}); trying sequence STORE",
                    uid,
                    type(exc).__name__,
                )
            else:
                if status == "OK":
                    return True

        try:
            imap_id = self._lookup_imap_id_by_uid(client, uid)
        except Exception as exc:
            logger.warning(
                "Email could not resolve UID {} for seen flag ({}); delivery will continue",
                uid,
                type(exc).__name__,
            )
            return False
        if imap_id is None:
            logger.warning("Email could not locate UID {} to mark it seen", uid)
            return False
        try:
            status, _ = client.store(imap_id, "+FLAGS", "\\Seen")
        except Exception as exc:
            logger.warning(
                "Email failed to mark UID {} as seen ({}); delivery will continue",
                uid,
                type(exc).__name__,
            )
            return False
        if status != "OK":
            logger.warning("Email failed to mark UID {} as seen", uid)
        return False

    @staticmethod
    def _extract_message_bytes(fetched: list[Any]) -> bytes | None:
        for item in fetched:
            if (
                isinstance(item, tuple)
                and len(item) >= 2
                and isinstance(item[1], (bytes, bytearray))
            ):
                return bytes(item[1])
        return None

    @staticmethod
    def _decode_header_value(value: str) -> str:
        if not value:
            return ""
        try:
            return str(make_header(decode_header(value)))
        except Exception:
            return value

    @classmethod
    def _extract_text_body(cls, msg: Any) -> str:
        """Best-effort extraction of readable body text."""
        if msg.is_multipart():
            plain_parts: list[str] = []
            html_parts: list[str] = []
            for part in msg.walk():
                if part.get_content_disposition() == "attachment":
                    continue
                content_type = part.get_content_type()
                try:
                    payload = part.get_content()
                except Exception:
                    payload_bytes = part.get_payload(decode=True) or b""
                    charset = part.get_content_charset() or "utf-8"
                    payload = payload_bytes.decode(charset, errors="replace")
                if not isinstance(payload, str):
                    continue
                if content_type == "text/plain":
                    plain_parts.append(payload)
                elif content_type == "text/html":
                    html_parts.append(payload)
            if plain_parts:
                return "\n\n".join(plain_parts).strip()
            if html_parts:
                return cls._html_to_text("\n\n".join(html_parts)).strip()
            return ""

        try:
            payload = msg.get_content()
        except Exception:
            payload_bytes = msg.get_payload(decode=True) or b""
            charset = msg.get_content_charset() or "utf-8"
            payload = payload_bytes.decode(charset, errors="replace")
        if not isinstance(payload, str):
            return ""
        if msg.get_content_type() == "text/html":
            return cls._html_to_text(payload).strip()
        return payload.strip()

    @staticmethod
    def _split_authentication_results(header: str) -> list[str] | None:
        """Split top-level Authentication-Results clauses, rejecting malformed CFWS."""
        clauses: list[str] = []
        current: list[str] = []
        quote: str | None = None
        comment_depth = 0
        escaped = False
        for char in header:
            if escaped:
                current.append(char)
                escaped = False
                continue
            if quote is not None:
                current.append(char)
                if char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                continue
            if comment_depth:
                current.append(char)
                if char == "\\":
                    escaped = True
                elif char == "(":
                    comment_depth += 1
                elif char == ")":
                    comment_depth -= 1
                continue
            if char == '"':
                quote = char
                current.append(char)
            elif char == "(":
                comment_depth = 1
                current.append(char)
            elif char == ")":
                return None
            elif char == ";":
                clauses.append("".join(current))
                current = []
            else:
                current.append(char)
        if quote is not None or comment_depth or escaped:
            return None
        clauses.append("".join(current))
        return clauses

    @staticmethod
    def _strip_authentication_comments(clause: str) -> str | None:
        """Remove nested RFC comments while preserving quoted values as single tokens."""
        result: list[str] = []
        quote: str | None = None
        comment_depth = 0
        escaped = False
        for char in clause:
            if escaped:
                if not comment_depth:
                    result.append(char)
                escaped = False
                continue
            if quote is not None:
                result.append(char)
                if char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                continue
            if comment_depth:
                if char == "\\":
                    escaped = True
                elif char == "(":
                    comment_depth += 1
                elif char == ")":
                    comment_depth -= 1
                result.append(" ")
                continue
            if char == '"':
                quote = char
                result.append(char)
            elif char == "(":
                comment_depth = 1
                result.append(" ")
            elif char == ")":
                return None
            else:
                result.append(char)
        if quote is not None or comment_depth or escaped:
            return None
        return "".join(result)

    @classmethod
    def _parse_authentication_clause(cls, clause: str) -> list[tuple[str, str]] | None:
        """Parse top-level name=value pairs without inspecting reason/comment text."""
        text = cls._strip_authentication_comments(clause)
        if text is None:
            return None
        pairs: list[tuple[str, str]] = []
        index = 0
        while index < len(text):
            while index < len(text) and text[index].isspace():
                index += 1
            if index == len(text):
                break
            name_match = re.match(r"[a-z][a-z0-9_-]*", text[index:], re.I)
            if name_match is None:
                return None
            name = name_match.group(0).lower()
            index += len(name_match.group(0))
            while index < len(text) and text[index].isspace():
                index += 1
            if index < len(text) and text[index] in {"/", "."}:
                separator = text[index]
                index += 1
                while index < len(text) and text[index].isspace():
                    index += 1
                suffix_match = re.match(r"[a-z0-9][a-z0-9_-]*", text[index:], re.I)
                if suffix_match is None:
                    return None
                name += separator + suffix_match.group(0).lower()
                index += len(suffix_match.group(0))
                while index < len(text) and text[index].isspace():
                    index += 1
            if index == len(text) or text[index] != "=":
                return None
            index += 1
            while index < len(text) and text[index].isspace():
                index += 1
            if index == len(text):
                return None

            if text[index] == '"':
                quote = text[index]
                index += 1
                value_chars: list[str] = []
                escaped = False
                while index < len(text):
                    char = text[index]
                    index += 1
                    if escaped:
                        value_chars.append(char)
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == quote:
                        break
                    else:
                        value_chars.append(char)
                else:
                    return None
                value = "".join(value_chars)
                if (
                    index < len(text)
                    and text[index] == "@"
                    and name in {"smtp.mailfrom", "header.i"}
                ):
                    domain_start = index
                    while index < len(text) and not text[index].isspace():
                        index += 1
                    suffix = text[domain_start:index]
                    if re.fullmatch(r"@[a-zA-Z0-9][a-zA-Z0-9.-]*", suffix) is None:
                        return None
                    value += suffix
                if escaped or (index < len(text) and not text[index].isspace()):
                    return None
            else:
                value_start = index
                while index < len(text) and not text[index].isspace():
                    index += 1
                value = text[value_start:index]
            if not value:
                return None
            pairs.append((name, value))
        if pairs:
            trailing_names = [name for name, _ in pairs[1:]]
            if any(name != "reason" and "." not in name for name in trailing_names):
                return None
            if len(trailing_names) != len(set(trailing_names)):
                return None
        return pairs

    @staticmethod
    def _authentication_domain(value: str) -> str:
        """Normalize one parsed Authentication-Results identity value."""
        candidate = value.strip().strip("\"'<>[]").rstrip(".")
        if "@" in candidate:
            candidate = candidate.rsplit("@", 1)[1]
        candidate = candidate.lower().rstrip(".")
        if not candidate or any(char.isspace() for char in candidate):
            return ""
        try:
            ascii_domain = candidate.encode("idna").decode("ascii")
            if not candidate.isascii() and ascii_domain.encode("ascii").decode("idna") != candidate:
                return ""
        except UnicodeError:
            return ""
        if len(ascii_domain) > 253 or any(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
            for label in ascii_domain.split(".")
        ):
            return ""
        return ascii_domain

    @staticmethod
    def _authentication_domains_align(sender_domain: str, identity_domain: str) -> bool:
        """Fail closed to exact alignment without a public-suffix-list dependency."""
        return bool(sender_domain and sender_domain == identity_domain)

    @classmethod
    def _authentication_clause_passes(
        cls,
        clauses: list[list[tuple[str, str]]],
        mechanism: str,
        identity_property: str,
        sender_domain: str,
    ) -> bool:
        for pairs in clauses:
            if not pairs or pairs[0][0].split("/", 1)[0] != mechanism:
                continue
            if pairs[0][1].lower() != "pass":
                continue
            properties = dict(pairs[1:])
            identity = properties.get(identity_property, "")
            if mechanism == "dkim":
                # AUID (header.i) is a standard receiver-reported DKIM
                # identity. Prefer header.d when supplied; never let AUID
                # override a malformed or unaligned signing domain.
                if identity_property in properties:
                    if any(char in identity for char in "@<>"):
                        continue
                else:
                    identity = properties.get("header.i", "")
                    if "@" not in identity:
                        continue
            if not identity:
                continue
            identity_domain = cls._authentication_domain(identity)
            if cls._authentication_domains_align(sender_domain, identity_domain):
                return True
        return False

    @classmethod
    def _check_authentication_results(cls, parsed_msg: Any) -> tuple[bool, bool]:
        """Evaluate the nearest mailbox Authentication-Results policy signal.

        This does not perform SPF, DKIM, or DMARC cryptography.  It trusts the
        first (nearest) Authentication-Results field to have been prepended by a
        receiving service that removes forged copies.  Pass results count only
        when their authenticated domain aligns with the RFC 5322 From domain;
        an explicit DMARC failure vetoes both results.

        Returns:
            A tuple of (spf_pass, dkim_pass) booleans.
        """
        sender = cls._parse_from_address(parsed_msg)
        sender_domain = sender.rsplit("@", 1)[1].rstrip(".") if "@" in sender else ""
        headers = parsed_msg.get_all("Authentication-Results") or []
        if not sender_domain or not headers:
            return False, False

        raw_clauses = cls._split_authentication_results(str(headers[0]))
        if raw_clauses is None or len(raw_clauses) < 2:
            return False, False
        clauses: list[list[tuple[str, str]]] = []
        for raw_clause in raw_clauses[1:]:
            parsed_clause = cls._parse_authentication_clause(raw_clause)
            if parsed_clause is None:
                return False, False
            if parsed_clause:
                clauses.append(parsed_clause)

        for pairs in clauses:
            if pairs[0][0].split("/", 1)[0] == "dmarc" and pairs[0][1].lower() == "fail":
                return False, False

        return (
            cls._authentication_clause_passes(
                clauses,
                "spf",
                "smtp.mailfrom",
                sender_domain,
            ),
            cls._authentication_clause_passes(
                clauses,
                "dkim",
                "header.d",
                sender_domain,
            ),
        )

    @classmethod
    def _extract_attachments(
        cls,
        msg: Any,
        uid: str,
        *,
        allowed_types: list[str],
        max_size: int,
        max_count: int,
    ) -> list[Path]:
        """Extract and save email attachments to the media directory.

        Returns list of saved file paths.
        """
        if not msg.is_multipart():
            return []

        saved: list[Path] = []
        media_dir = get_media_dir("email")

        for part in msg.walk():
            if len(saved) >= max_count:
                break
            if part.get_content_disposition() != "attachment":
                continue

            content_type = part.get_content_type()
            if not any(fnmatch(content_type, pat) for pat in allowed_types):
                logger.debug(
                    "Email attachment skipped (type {}): not in allowed list", content_type
                )
                continue

            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            if len(payload) > max_size:
                logger.warning(
                    "Email attachment skipped: size {} exceeds limit {}",
                    len(payload),
                    max_size,
                )
                continue

            raw_name = part.get_filename() or "attachment"
            sanitized = safe_filename(raw_name) or "attachment"
            dest = media_dir / f"{uid}_{sanitized}"

            try:
                dest.write_bytes(payload)
                saved.append(dest)
                logger.info("Email attachment saved: {}", dest)
            except Exception as exc:
                logger.warning("Failed to save email attachment {}: {}", dest, exc)

        return saved

    @staticmethod
    def _html_to_text(raw_html: str) -> str:
        text = re.sub(r"<\s*br\s*/?>", "\n", raw_html, flags=re.IGNORECASE)
        text = re.sub(r"<\s*/\s*p\s*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        return html.unescape(text)

    def _reply_subject(self, base_subject: str) -> str:
        subject = (base_subject or "").strip() or "hahobot reply"
        prefix = self.config.subject_prefix or "Re: "
        if subject.lower().startswith("re:"):
            return subject
        return f"{prefix}{subject}"
