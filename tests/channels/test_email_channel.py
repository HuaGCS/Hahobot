import asyncio
import imaplib
import threading
from datetime import date
from email.message import EmailMessage
from pathlib import Path

import pytest

from hahobot.bus.events import OutboundMessage
from hahobot.bus.queue import MessageBus
from hahobot.channels.email import EmailChannel
from hahobot.config.schema import EmailConfig


def _make_config(**overrides) -> EmailConfig:
    defaults = {
        "enabled": True,
        "consent_granted": True,
        "imap_host": "imap.example.com",
        "imap_port": 993,
        "imap_username": "bot@example.com",
        "imap_password": "secret",
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_username": "bot@example.com",
        "smtp_password": "secret",
        "mark_seen": True,
        "allow_from": ["*"],
        # Disable auth verification by default so existing tests are unaffected
        "verify_dkim": False,
        "verify_spf": False,
    }
    defaults.update(overrides)
    return EmailConfig(**defaults)


def _make_raw_email(
    from_addr: str = "alice@example.com",
    subject: str = "Hello",
    body: str = "This is the body.",
    auth_results: str | None = None,
) -> bytes:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = "bot@example.com"
    msg["Subject"] = subject
    msg["Message-ID"] = "<m1@example.com>"
    if auth_results:
        msg["Authentication-Results"] = auth_results
    msg.set_content(body)
    return msg.as_bytes()


def _install_fake_smtp(monkeypatch) -> list:
    """Patch smtplib.SMTP with a capturing fake; return the list of sent EmailMessages."""
    sent: list[EmailMessage] = []

    class FakeSMTP:
        def __init__(self, _host: str, _port: int, timeout: int = 30) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def starttls(self, context=None):
            return None

        def login(self, _user: str, _pw: str):
            return None

        def send_message(self, msg: EmailMessage):
            sent.append(msg)

    monkeypatch.setattr(
        "hahobot.channels.email.smtplib.SMTP",
        lambda host, port, timeout=30: FakeSMTP(host, port, timeout=timeout),
    )
    return sent


def test_fetch_new_messages_parses_unseen_and_marks_seen(monkeypatch) -> None:
    raw = _make_raw_email(subject="Invoice", body="Please pay")

    class FakeIMAP:
        def __init__(self) -> None:
            self.store_calls: list[tuple[bytes, str, str]] = []
            self.uid_calls: list[tuple] = []

        def login(self, _user: str, _pw: str):
            return "OK", [b"logged in"]

        def select(self, _mailbox: str):
            return "OK", [b"1"]

        def search(self, *_args):
            return "OK", [b"1"]

        def fetch(self, _imap_id: bytes, _parts: str):
            return "OK", [(b"1 (UID 123 BODY[] {200})", raw), b")"]

        def uid(self, command: str, *args):
            self.uid_calls.append((command, *args))
            if command == "SEARCH":
                return "OK", [b"123"]
            if command == "FETCH":
                return "OK", [(b"1 (UID 123 BODY[] {200})", raw), b")"]
            if command == "STORE":
                return "OK", [b""]
            raise AssertionError(f"unexpected UID command: {command}")

        def store(self, imap_id: bytes, op: str, flags: str):
            self.store_calls.append((imap_id, op, flags))
            return "OK", [b""]

        def logout(self):
            return "BYE", [b""]

    fake = FakeIMAP()
    connection_kwargs: dict[str, object] = {}

    def _open_imap(_host: str, _port: int, **kwargs):
        connection_kwargs.update(kwargs)
        return fake

    monkeypatch.setattr("hahobot.channels.email.imaplib.IMAP4_SSL", _open_imap)

    channel = EmailChannel(_make_config(), MessageBus())
    items = channel._fetch_new_messages()

    assert len(items) == 1
    assert items[0]["sender"] == "alice@example.com"
    assert items[0]["subject"] == "Invoice"
    assert "Please pay" in items[0]["content"]
    assert ("STORE", "123", "+FLAGS", "(\\Seen)") in fake.uid_calls
    assert [call for call in fake.uid_calls if call[0] == "FETCH"] == [
        ("FETCH", "123", "(BODY.PEEK[HEADER])"),
        ("FETCH", "123", "(BODY.PEEK[])"),
    ]
    assert connection_kwargs == {"timeout": EmailChannel._IMAP_TIMEOUT_SECONDS}

    # Same UID should be deduped in-process.
    items_again = channel._fetch_new_messages()
    assert items_again == []
    assert len([call for call in fake.uid_calls if call[0] == "FETCH"]) == 2


def test_fetch_new_messages_retries_once_when_imap_connection_goes_stale(monkeypatch) -> None:
    raw = _make_raw_email(subject="Invoice", body="Please pay")
    fail_once = {"pending": True}

    class FlakyIMAP:
        def __init__(self) -> None:
            self.store_calls: list[tuple[bytes, str, str]] = []
            self.search_calls = 0

        def login(self, _user: str, _pw: str):
            return "OK", [b"logged in"]

        def select(self, _mailbox: str):
            return "OK", [b"1"]

        def search(self, *_args):
            return "OK", [b"1"]

        def fetch(self, _imap_id: bytes, _parts: str):
            return "OK", [(b"1 (UID 123 BODY[] {200})", raw), b")"]

        def uid(self, command: str, *args):
            if command == "SEARCH":
                self.search_calls += 1
                if fail_once["pending"]:
                    fail_once["pending"] = False
                    raise imaplib.IMAP4.abort("socket error")
                return "OK", [b"123"]
            if command == "FETCH":
                return "OK", [(b"1 (UID 123 BODY[] {200})", raw), b")"]
            if command == "STORE":
                return "OK", [b""]
            raise AssertionError(f"unexpected UID command: {command}")

        def store(self, imap_id: bytes, op: str, flags: str):
            self.store_calls.append((imap_id, op, flags))
            return "OK", [b""]

        def logout(self):
            return "BYE", [b""]

    fake_instances: list[FlakyIMAP] = []

    def _factory(_host: str, _port: int, **_kwargs):
        instance = FlakyIMAP()
        fake_instances.append(instance)
        return instance

    monkeypatch.setattr("hahobot.channels.email.imaplib.IMAP4_SSL", _factory)

    channel = EmailChannel(_make_config(), MessageBus())
    items = channel._fetch_new_messages()

    assert len(items) == 1
    assert len(fake_instances) == 2
    assert fake_instances[0].search_calls == 1
    assert fake_instances[1].search_calls == 1


def test_fetch_new_messages_keeps_messages_collected_before_stale_retry(monkeypatch) -> None:
    raw_first = _make_raw_email(subject="First", body="First body")
    raw_second = _make_raw_email(subject="Second", body="Second body")
    mailbox_state = {"123": raw_first, "124": raw_second}
    fail_once = {"pending": True}

    class FlakyIMAP:
        def login(self, _user: str, _pw: str):
            return "OK", [b"logged in"]

        def select(self, _mailbox: str):
            return "OK", [b"2"]

        def search(self, *_args):
            return "OK", [b"1"]

        def fetch(self, imap_id: bytes, _parts: str):
            if imap_id == b"2" and fail_once["pending"]:
                fail_once["pending"] = False
                raise imaplib.IMAP4.abort("socket error")
            return "OK", [(b"1 (UID 123 BODY[] {200})", raw_first), b")"]

        def uid(self, command: str, *args):
            if command == "SEARCH":
                return "OK", [b"123 124"]
            if command == "FETCH":
                uid = args[0]
                if uid == "124" and fail_once["pending"]:
                    fail_once["pending"] = False
                    raise imaplib.IMAP4.abort("socket error")
                raw = mailbox_state[uid]
                return "OK", [(f"1 (UID {uid} BODY[] {{200}})".encode(), raw), b")"]
            if command == "STORE":
                return "OK", [b""]
            raise AssertionError(f"unexpected UID command: {command}")

        def store(self, imap_id: bytes, _op: str, _flags: str):
            return "OK", [b""]

        def logout(self):
            return "BYE", [b""]

    monkeypatch.setattr(
        "hahobot.channels.email.imaplib.IMAP4_SSL", lambda _h, _p, **_kwargs: FlakyIMAP()
    )

    channel = EmailChannel(_make_config(), MessageBus())
    items = channel._fetch_new_messages()

    assert [item["subject"] for item in items] == ["First", "Second"]


def test_fetch_new_messages_skips_missing_mailbox(monkeypatch) -> None:
    class MissingMailboxIMAP:
        def login(self, _user: str, _pw: str):
            return "OK", [b"logged in"]

        def select(self, _mailbox: str):
            raise imaplib.IMAP4.error("Mailbox doesn't exist")

        def logout(self):
            return "BYE", [b""]

    monkeypatch.setattr(
        "hahobot.channels.email.imaplib.IMAP4_SSL",
        lambda _h, _p, **_kwargs: MissingMailboxIMAP(),
    )

    channel = EmailChannel(_make_config(), MessageBus())

    assert channel._fetch_new_messages() == []


def test_extract_text_body_falls_back_to_html() -> None:
    msg = EmailMessage()
    msg["From"] = "alice@example.com"
    msg["To"] = "bot@example.com"
    msg["Subject"] = "HTML only"
    msg.add_alternative("<p>Hello<br>world</p>", subtype="html")

    text = EmailChannel._extract_text_body(msg)
    assert "Hello" in text
    assert "world" in text


@pytest.mark.asyncio
async def test_start_returns_immediately_without_consent(monkeypatch) -> None:
    cfg = _make_config()
    cfg.consent_granted = False
    channel = EmailChannel(cfg, MessageBus())

    called = {"fetch": False}

    def _fake_fetch():
        called["fetch"] = True
        return []

    monkeypatch.setattr(channel, "_fetch_new_messages", _fake_fetch)
    await channel.start()
    assert channel.is_running is False
    assert called["fetch"] is False


@pytest.mark.asyncio
async def test_send_uses_smtp_and_reply_subject(monkeypatch) -> None:
    class FakeSMTP:
        def __init__(self, _host: str, _port: int, timeout: int = 30) -> None:
            self.timeout = timeout
            self.started_tls = False
            self.logged_in = False
            self.sent_messages: list[EmailMessage] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def starttls(self, context=None):
            self.started_tls = True

        def login(self, _user: str, _pw: str):
            self.logged_in = True

        def send_message(self, msg: EmailMessage):
            self.sent_messages.append(msg)

    fake_instances: list[FakeSMTP] = []

    def _smtp_factory(host: str, port: int, timeout: int = 30):
        instance = FakeSMTP(host, port, timeout=timeout)
        fake_instances.append(instance)
        return instance

    monkeypatch.setattr("hahobot.channels.email.smtplib.SMTP", _smtp_factory)

    channel = EmailChannel(_make_config(), MessageBus())
    channel._last_subject_by_chat["alice@example.com"] = "Invoice #42"
    channel._last_message_id_by_chat["alice@example.com"] = "<m1@example.com>"

    await channel.send(
        OutboundMessage(
            channel="email",
            chat_id="alice@example.com",
            content="Acknowledged.",
        )
    )

    assert len(fake_instances) == 1
    smtp = fake_instances[0]
    assert smtp.started_tls is True
    assert smtp.logged_in is True
    assert len(smtp.sent_messages) == 1
    sent = smtp.sent_messages[0]
    assert sent["Subject"] == "Re: Invoice #42"
    assert sent["To"] == "alice@example.com"
    assert sent["In-Reply-To"] == "<m1@example.com>"


@pytest.mark.asyncio
async def test_send_attaches_outbound_media(monkeypatch, tmp_path) -> None:
    sent = _install_fake_smtp(monkeypatch)
    img = tmp_path / "scene.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake-bytes")

    channel = EmailChannel(_make_config(), MessageBus())
    channel._last_subject_by_chat["alice@example.com"] = "Hi"
    await channel.send(
        OutboundMessage(
            channel="email",
            chat_id="alice@example.com",
            content="Here is your image.",
            media=[str(img)],
            metadata={"force_send": True},
        )
    )

    assert len(sent) == 1
    attachments = list(sent[0].iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "scene.png"
    assert attachments[0].get_content_type() == "image/png"
    assert attachments[0].get_content() == b"\x89PNG\r\n\x1a\nfake-bytes"


@pytest.mark.asyncio
async def test_send_skips_oversized_media_but_still_sends_body(monkeypatch, tmp_path) -> None:
    sent = _install_fake_smtp(monkeypatch)
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 100)

    cfg = _make_config(max_attachment_size=10)
    channel = EmailChannel(cfg, MessageBus())
    channel._last_subject_by_chat["alice@example.com"] = "Hi"
    await channel.send(
        OutboundMessage(
            channel="email",
            chat_id="alice@example.com",
            content="Body still goes out.",
            media=[str(big)],
            metadata={"force_send": True},
        )
    )

    assert len(sent) == 1
    assert list(sent[0].iter_attachments()) == []


@pytest.mark.asyncio
async def test_send_skips_missing_media_file(monkeypatch, tmp_path) -> None:
    sent = _install_fake_smtp(monkeypatch)
    channel = EmailChannel(_make_config(), MessageBus())
    channel._last_subject_by_chat["alice@example.com"] = "Hi"
    await channel.send(
        OutboundMessage(
            channel="email",
            chat_id="alice@example.com",
            content="No file here.",
            media=[str(tmp_path / "does-not-exist.png")],
            metadata={"force_send": True},
        )
    )

    assert len(sent) == 1
    assert list(sent[0].iter_attachments()) == []


@pytest.mark.asyncio
async def test_send_respects_max_attachments_per_email(monkeypatch, tmp_path) -> None:
    sent = _install_fake_smtp(monkeypatch)
    files = []
    for i in range(3):
        f = tmp_path / f"f{i}.txt"
        f.write_bytes(b"data")
        files.append(str(f))

    cfg = _make_config(max_attachments_per_email=2)
    channel = EmailChannel(cfg, MessageBus())
    channel._last_subject_by_chat["alice@example.com"] = "Hi"
    await channel.send(
        OutboundMessage(
            channel="email",
            chat_id="alice@example.com",
            content="Three files, cap two.",
            media=files,
            metadata={"force_send": True},
        )
    )

    assert len(sent) == 1
    assert len(list(sent[0].iter_attachments())) == 2


@pytest.mark.asyncio
async def test_send_skips_reply_when_auto_reply_disabled(monkeypatch) -> None:
    """When auto_reply_enabled=False, replies should be skipped but proactive sends allowed."""

    class FakeSMTP:
        def __init__(self, _host: str, _port: int, timeout: int = 30) -> None:
            self.sent_messages: list[EmailMessage] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def starttls(self, context=None):
            return None

        def login(self, _user: str, _pw: str):
            return None

        def send_message(self, msg: EmailMessage):
            self.sent_messages.append(msg)

    fake_instances: list[FakeSMTP] = []

    def _smtp_factory(host: str, port: int, timeout: int = 30):
        instance = FakeSMTP(host, port, timeout=timeout)
        fake_instances.append(instance)
        return instance

    monkeypatch.setattr("hahobot.channels.email.smtplib.SMTP", _smtp_factory)

    cfg = _make_config()
    cfg.auto_reply_enabled = False
    channel = EmailChannel(cfg, MessageBus())

    # Mark alice as someone who sent us an email (making this a "reply")
    channel._last_subject_by_chat["alice@example.com"] = "Previous email"

    # Reply should be skipped (auto_reply_enabled=False)
    await channel.send(
        OutboundMessage(
            channel="email",
            chat_id="alice@example.com",
            content="Should not send.",
        )
    )
    assert fake_instances == []

    # Reply with force_send=True should be sent
    await channel.send(
        OutboundMessage(
            channel="email",
            chat_id="alice@example.com",
            content="Force send.",
            metadata={"force_send": True},
        )
    )
    assert len(fake_instances) == 1
    assert len(fake_instances[0].sent_messages) == 1


@pytest.mark.asyncio
async def test_send_proactive_email_when_auto_reply_disabled(monkeypatch) -> None:
    """Proactive emails (not replies) should be sent even when auto_reply_enabled=False."""

    class FakeSMTP:
        def __init__(self, _host: str, _port: int, timeout: int = 30) -> None:
            self.sent_messages: list[EmailMessage] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def starttls(self, context=None):
            return None

        def login(self, _user: str, _pw: str):
            return None

        def send_message(self, msg: EmailMessage):
            self.sent_messages.append(msg)

    fake_instances: list[FakeSMTP] = []

    def _smtp_factory(host: str, port: int, timeout: int = 30):
        instance = FakeSMTP(host, port, timeout=timeout)
        fake_instances.append(instance)
        return instance

    monkeypatch.setattr("hahobot.channels.email.smtplib.SMTP", _smtp_factory)

    cfg = _make_config()
    cfg.auto_reply_enabled = False
    channel = EmailChannel(cfg, MessageBus())

    # bob@example.com has never sent us an email (proactive send)
    # This should be sent even with auto_reply_enabled=False
    await channel.send(
        OutboundMessage(
            channel="email",
            chat_id="bob@example.com",
            content="Hello, this is a proactive email.",
        )
    )
    assert len(fake_instances) == 1
    assert len(fake_instances[0].sent_messages) == 1
    sent = fake_instances[0].sent_messages[0]
    assert sent["To"] == "bob@example.com"


@pytest.mark.asyncio
async def test_send_skips_progress_messages(monkeypatch) -> None:
    # Progress / tool-hint updates must never produce an email (would otherwise send a
    # near-empty email after each tool call). Ported from nanobot cbf1ede.
    called = {"smtp": False}

    def _smtp_factory(host: str, port: int, timeout: int = 30):
        called["smtp"] = True
        raise AssertionError("SMTP should not be opened for a progress message")

    monkeypatch.setattr("hahobot.channels.email.smtplib.SMTP", _smtp_factory)

    channel = EmailChannel(_make_config(), MessageBus())
    # Pre-seed a known subject so the message would otherwise be a valid reply.
    channel._last_subject_by_chat["alice@example.com"] = "Hello"

    for meta in (
        {"_progress": True},
        {"_progress": True, "_tool_hint": True},
        {"_progress": True, "force_send": True},
    ):
        await channel.send(
            OutboundMessage(
                channel="email",
                chat_id="alice@example.com",
                content='read_file("x")',
                metadata=meta,
            )
        )

    assert called["smtp"] is False


async def test_send_skips_when_consent_not_granted(monkeypatch) -> None:
    class FakeSMTP:
        def __init__(self, _host: str, _port: int, timeout: int = 30) -> None:
            self.sent_messages: list[EmailMessage] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def starttls(self, context=None):
            return None

        def login(self, _user: str, _pw: str):
            return None

        def send_message(self, msg: EmailMessage):
            self.sent_messages.append(msg)

    called = {"smtp": False}

    def _smtp_factory(host: str, port: int, timeout: int = 30):
        called["smtp"] = True
        return FakeSMTP(host, port, timeout=timeout)

    monkeypatch.setattr("hahobot.channels.email.smtplib.SMTP", _smtp_factory)

    cfg = _make_config()
    cfg.consent_granted = False
    channel = EmailChannel(cfg, MessageBus())
    await channel.send(
        OutboundMessage(
            channel="email",
            chat_id="alice@example.com",
            content="Should not send.",
            metadata={"force_send": True},
        )
    )
    assert called["smtp"] is False


def test_fetch_messages_between_dates_uses_imap_since_before_without_mark_seen(monkeypatch) -> None:
    raw = _make_raw_email(subject="Status", body="Yesterday update")

    class FakeIMAP:
        def __init__(self) -> None:
            self.search_args = None
            self.store_calls: list[tuple[bytes, str, str]] = []
            self.uid_calls: list[tuple] = []

        def login(self, _user: str, _pw: str):
            return "OK", [b"logged in"]

        def select(self, _mailbox: str):
            return "OK", [b"1"]

        def search(self, *_args):
            self.search_args = _args
            return "OK", [b"5"]

        def fetch(self, _imap_id: bytes, _parts: str):
            return "OK", [(b"5 (UID 999 BODY[] {200})", raw), b")"]

        def uid(self, command: str, *args):
            self.uid_calls.append((command, *args))
            if command == "SEARCH":
                self.search_args = args
                return "OK", [b"999"]
            if command == "FETCH":
                return "OK", [(b"5 (UID 999 BODY[] {200})", raw), b")"]
            if command == "STORE":
                return "OK", [b""]
            raise AssertionError(f"unexpected UID command: {command}")

        def store(self, imap_id: bytes, op: str, flags: str):
            self.store_calls.append((imap_id, op, flags))
            return "OK", [b""]

        def logout(self):
            return "BYE", [b""]

    fake = FakeIMAP()
    monkeypatch.setattr("hahobot.channels.email.imaplib.IMAP4_SSL", lambda _h, _p, **_kwargs: fake)

    channel = EmailChannel(_make_config(), MessageBus())
    items = channel.fetch_messages_between_dates(
        start_date=date(2026, 2, 6),
        end_date=date(2026, 2, 7),
        limit=10,
    )

    assert len(items) == 1
    assert items[0]["subject"] == "Status"
    # uid("SEARCH", None, "SINCE", "06-Feb-2026", "BEFORE", "07-Feb-2026")
    assert fake.search_args is not None
    assert fake.search_args[1:] == ("SINCE", "06-Feb-2026", "BEFORE", "07-Feb-2026")
    assert fake.store_calls == []
    assert not [call for call in fake.uid_calls if call[0] == "STORE"]


# ---------------------------------------------------------------------------
# Security: Anti-spoofing tests for Authentication-Results verification
# ---------------------------------------------------------------------------


def _make_fake_imap(
    raw: bytes,
    *,
    uid_store_status: str = "OK",
    uid_store_error: Exception | None = None,
    sequence_store_error: Exception | None = None,
    uid_validity: str | None = None,
):
    """Return a FakeIMAP class pre-loaded with the given raw email."""

    class FakeIMAP:
        def __init__(self) -> None:
            self.store_calls: list[tuple[bytes, str, str]] = []
            self.uid_calls: list[tuple] = []
            self.uid_validity = uid_validity

        def login(self, _user: str, _pw: str):
            return "OK", [b"logged in"]

        def select(self, _mailbox: str):
            return "OK", [b"1"]

        def search(self, *_args):
            return "OK", [b"1"]

        def response(self, code: str):
            assert code == "UIDVALIDITY"
            data = [] if self.uid_validity is None else [self.uid_validity.encode("ascii")]
            return code, data

        def fetch(self, _imap_id: bytes, _parts: str):
            return "OK", [(b"1 (UID 500 BODY[] {200})", raw), b")"]

        def uid(self, command: str, *args):
            self.uid_calls.append((command, *args))
            if command == "SEARCH":
                return "OK", [b"500"]
            if command == "FETCH":
                return "OK", [(b"1 (UID 500 BODY[] {200})", raw), b")"]
            if command == "STORE":
                if uid_store_error is not None:
                    raise uid_store_error
                return uid_store_status, [b""]
            raise AssertionError(f"unexpected UID command: {command}")

        def store(self, imap_id: bytes, op: str, flags: str):
            self.store_calls.append((imap_id, op, flags))
            if sequence_store_error is not None:
                raise sequence_store_error
            return "OK", [b""]

        def logout(self):
            return "BYE", [b""]

    return FakeIMAP()


def test_fetch_new_messages_rejects_unauthorized_sender_before_body_or_attachments(
    monkeypatch,
) -> None:
    raw = _make_raw_email_with_attachment(from_addr="blocked@example.com")
    fake = _make_fake_imap(raw)
    monkeypatch.setattr("hahobot.channels.email.imaplib.IMAP4_SSL", lambda _h, _p, **_kwargs: fake)

    attachment_calls = 0

    def _extract_attachments(*_args, **_kwargs):
        nonlocal attachment_calls
        attachment_calls += 1
        return []

    monkeypatch.setattr(EmailChannel, "_extract_attachments", _extract_attachments)
    channel = EmailChannel(
        _make_config(
            allow_from=["allowed@example.com"],
            allowed_attachment_types=["application/pdf"],
        ),
        MessageBus(),
    )

    assert channel._fetch_new_messages() == []
    assert attachment_calls == 0
    assert [call for call in fake.uid_calls if call[0] == "FETCH"] == [
        ("FETCH", "500", "(BODY.PEEK[HEADER])")
    ]
    assert ("STORE", "500", "+FLAGS", "(\\Seen)") in fake.uid_calls

    # The UID is rejected before any FETCH on later polls in this process.
    assert channel._fetch_new_messages() == []
    assert len([call for call in fake.uid_calls if call[0] == "FETCH"]) == 1


def test_fetch_new_messages_skips_self_sent_mail_before_body(monkeypatch) -> None:
    raw = _make_raw_email(from_addr="Hahobot <BOT@example.com>", subject="Loop")
    fake = _make_fake_imap(raw)
    monkeypatch.setattr("hahobot.channels.email.imaplib.IMAP4_SSL", lambda _h, _p, **_kwargs: fake)

    channel = EmailChannel(_make_config(), MessageBus())

    assert channel._fetch_new_messages() == []
    assert [call for call in fake.uid_calls if call[0] == "FETCH"] == [
        ("FETCH", "500", "(BODY.PEEK[HEADER])")
    ]
    assert ("STORE", "500", "+FLAGS", "(\\Seen)") in fake.uid_calls


def test_fetch_new_messages_dedupes_missing_from_before_body(monkeypatch) -> None:
    raw = _make_raw_email(from_addr="", subject="Malformed")
    fake = _make_fake_imap(raw)
    monkeypatch.setattr("hahobot.channels.email.imaplib.IMAP4_SSL", lambda _h, _p, **_kwargs: fake)

    channel = EmailChannel(_make_config(), MessageBus())

    assert channel._fetch_new_messages() == []
    assert channel._fetch_new_messages() == []
    assert [call for call in fake.uid_calls if call[0] == "FETCH"] == [
        ("FETCH", "500", "(BODY.PEEK[HEADER])")
    ]


@pytest.mark.parametrize("from_addr", ["not-an-email", "alice@example.com, bob@example.com"])
def test_fetch_new_messages_dedupes_malformed_from_before_body(
    from_addr: str,
    monkeypatch,
) -> None:
    raw = _make_raw_email(from_addr=from_addr, subject="Malformed")
    fake = _make_fake_imap(raw)
    monkeypatch.setattr(
        "hahobot.channels.email.imaplib.IMAP4_SSL",
        lambda _h, _p, **_kwargs: fake,
    )

    channel = EmailChannel(_make_config(), MessageBus())

    assert channel._fetch_new_messages() == []
    assert channel._fetch_new_messages() == []
    assert [call for call in fake.uid_calls if call[0] == "FETCH"] == [
        ("FETCH", "500", "(BODY.PEEK[HEADER])")
    ]


def test_fetch_new_messages_resets_dedupe_when_uidvalidity_changes(monkeypatch) -> None:
    raw = _make_raw_email()
    fake = _make_fake_imap(raw, uid_validity="100")
    monkeypatch.setattr("hahobot.channels.email.imaplib.IMAP4_SSL", lambda _h, _p, **_kwargs: fake)

    channel = EmailChannel(_make_config(), MessageBus())

    assert len(channel._fetch_new_messages()) == 1
    assert channel._fetch_new_messages() == []

    fake.uid_validity = "101"
    assert len(channel._fetch_new_messages()) == 1
    assert len([call for call in fake.uid_calls if call[0] == "FETCH"]) == 4


def test_fetch_new_messages_resets_unknown_uid_namespace_when_it_becomes_known(
    monkeypatch,
) -> None:
    raw = _make_raw_email()
    fake = _make_fake_imap(raw)
    monkeypatch.setattr(
        "hahobot.channels.email.imaplib.IMAP4_SSL",
        lambda _h, _p, **_kwargs: fake,
    )

    channel = EmailChannel(_make_config(), MessageBus())

    assert len(channel._fetch_new_messages()) == 1
    assert channel._fetch_new_messages() == []

    fake.uid_validity = "101"
    assert len(channel._fetch_new_messages()) == 1
    assert len([call for call in fake.uid_calls if call[0] == "FETCH"]) == 4


@pytest.mark.asyncio
async def test_stop_wakes_email_poll_sleep(monkeypatch) -> None:
    channel = EmailChannel(
        _make_config(poll_interval_seconds=300),
        MessageBus(),
    )
    poll_started = asyncio.Event()

    async def _empty_poll(_func, /, *_args, **_kwargs):
        poll_started.set()
        return []

    monkeypatch.setattr(channel, "_run_blocking", _empty_poll)

    start_task = asyncio.create_task(channel.start())
    await poll_started.wait()
    await channel.stop()
    await asyncio.wait_for(start_task, timeout=1)


@pytest.mark.asyncio
async def test_stop_drains_inflight_poll_results_and_serializes_restart(monkeypatch) -> None:
    channel = EmailChannel(
        _make_config(poll_interval_seconds=300),
        MessageBus(),
    )
    first_poll_started = asyncio.Event()
    release_first_poll = asyncio.Event()
    second_poll_started = asyncio.Event()
    active_polls = 0
    max_active_polls = 0
    poll_calls = 0

    async def _controlled_poll(_func, _stop_event, /):
        nonlocal active_polls, max_active_polls, poll_calls
        poll_calls += 1
        active_polls += 1
        max_active_polls = max(max_active_polls, active_polls)
        try:
            if poll_calls == 1:
                first_poll_started.set()
                await release_first_poll.wait()
                return [
                    {
                        "sender": "alice@example.com",
                        "content": "committed before stop",
                    }
                ]
            second_poll_started.set()
            return []
        finally:
            active_polls -= 1

    delivered: list[str] = []

    async def _record_delivery(**kwargs) -> None:
        delivered.append(kwargs["content"])

    monkeypatch.setattr(channel, "_run_blocking", _controlled_poll)
    monkeypatch.setattr(channel, "_handle_message", _record_delivery)

    first_start = asyncio.create_task(channel.start())
    await first_poll_started.wait()
    first_stop = asyncio.create_task(channel.stop())
    await asyncio.sleep(0)
    second_start = asyncio.create_task(channel.start())
    await asyncio.sleep(0)

    assert not first_stop.done()
    assert not second_poll_started.is_set()

    release_first_poll.set()
    await asyncio.wait_for(first_stop, timeout=1)
    await asyncio.wait_for(second_poll_started.wait(), timeout=1)

    assert delivered == ["committed before stop"]
    assert max_active_polls == 1

    await channel.stop()
    await asyncio.wait_for(first_start, timeout=1)
    await asyncio.wait_for(second_start, timeout=1)


@pytest.mark.asyncio
async def test_cancelling_start_notifies_inflight_worker(monkeypatch) -> None:
    channel = EmailChannel(_make_config(), MessageBus())
    poll_started = asyncio.Event()
    received_stop_events: list[threading.Event] = []

    async def _blocked_poll(_func, stop_event, /):
        received_stop_events.append(stop_event)
        poll_started.set()
        while not stop_event.is_set():
            await asyncio.sleep(0)
        return []

    monkeypatch.setattr(channel, "_run_blocking", _blocked_poll)

    start_task = asyncio.create_task(channel.start())
    await poll_started.wait()
    start_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await start_task

    assert len(received_stop_events) == 1
    assert received_stop_events[0].is_set()


@pytest.mark.asyncio
async def test_cancelling_start_drains_committed_batch(monkeypatch) -> None:
    channel = EmailChannel(_make_config(), MessageBus())
    first_delivery_started = asyncio.Event()
    release_first_delivery = asyncio.Event()
    delivered: list[str] = []

    async def _committed_poll(_func, _stop_event, /):
        return [
            {"sender": "alice@example.com", "content": "first"},
            {"sender": "bob@example.com", "content": "second"},
        ]

    async def _controlled_delivery(**kwargs) -> None:
        if kwargs["content"] == "first":
            first_delivery_started.set()
            await release_first_delivery.wait()
        delivered.append(kwargs["content"])

    monkeypatch.setattr(channel, "_run_blocking", _committed_poll)
    monkeypatch.setattr(channel, "_handle_message", _controlled_delivery)

    start_task = asyncio.create_task(channel.start())
    await first_delivery_started.wait()
    start_task.cancel()
    await asyncio.sleep(0)

    assert not start_task.done()
    assert delivered == []

    release_first_delivery.set()
    with pytest.raises(asyncio.CancelledError):
        await start_task

    assert delivered == ["first", "second"]


def test_fetch_new_messages_falls_back_to_sequence_store_for_seen(monkeypatch) -> None:
    raw = _make_raw_email()
    fake = _make_fake_imap(raw, uid_store_status="BAD")
    monkeypatch.setattr("hahobot.channels.email.imaplib.IMAP4_SSL", lambda _h, _p, **_kwargs: fake)

    channel = EmailChannel(_make_config(), MessageBus())

    assert len(channel._fetch_new_messages()) == 1
    assert ("STORE", "500", "+FLAGS", "(\\Seen)") in fake.uid_calls
    assert fake.store_calls == [(b"1", "+FLAGS", "\\Seen")]


def test_fetch_new_messages_falls_back_when_uid_store_raises(monkeypatch) -> None:
    raw = _make_raw_email()
    fake = _make_fake_imap(
        raw,
        uid_store_error=imaplib.IMAP4.error("UID STORE unsupported"),
    )
    monkeypatch.setattr("hahobot.channels.email.imaplib.IMAP4_SSL", lambda _h, _p, **_kwargs: fake)

    channel = EmailChannel(_make_config(), MessageBus())

    assert len(channel._fetch_new_messages()) == 1
    assert fake.store_calls == [(b"1", "+FLAGS", "\\Seen")]


def test_seen_flag_failures_do_not_discard_an_accepted_message(monkeypatch) -> None:
    raw = _make_raw_email()
    fake = _make_fake_imap(
        raw,
        uid_store_error=imaplib.IMAP4.error("UID STORE unsupported"),
        sequence_store_error=imaplib.IMAP4.error("STORE denied"),
    )
    monkeypatch.setattr("hahobot.channels.email.imaplib.IMAP4_SSL", lambda _h, _p, **_kwargs: fake)

    channel = EmailChannel(_make_config(), MessageBus())

    items = channel._fetch_new_messages()
    assert len(items) == 1
    assert items[0]["sender"] == "alice@example.com"
    assert "500" in channel._processed_uids

    # Process-local UID dedupe prevents a second delivery even though the
    # server would not allow either STORE form.
    assert channel._fetch_new_messages() == []


def test_spoofed_email_rejected_when_verify_enabled(monkeypatch) -> None:
    """An email without Authentication-Results should be rejected when verify_dkim=True."""
    raw = _make_raw_email(subject="Spoofed", body="Malicious payload")
    fake = _make_fake_imap(raw)
    monkeypatch.setattr("hahobot.channels.email.imaplib.IMAP4_SSL", lambda _h, _p, **_kwargs: fake)

    cfg = _make_config(verify_dkim=True, verify_spf=True)
    channel = EmailChannel(cfg, MessageBus())
    items = channel._fetch_new_messages()

    assert len(items) == 0, "Spoofed email without auth headers should be rejected"
    assert [call for call in fake.uid_calls if call[0] == "FETCH"] == [
        ("FETCH", "500", "(BODY.PEEK[HEADER])")
    ]
    assert not [call for call in fake.uid_calls if call[0] == "STORE"]

    # Authentication failures stay unseen for the mailbox owner but are not
    # repeatedly fetched and logged during this process lifetime.
    assert channel._fetch_new_messages() == []
    assert len([call for call in fake.uid_calls if call[0] == "FETCH"]) == 1


def test_email_with_valid_auth_results_accepted(monkeypatch) -> None:
    """An email with spf=pass and dkim=pass should be accepted."""
    raw = _make_raw_email(
        subject="Legit",
        body="Hello from verified sender",
        auth_results="mx.example.com; spf=pass smtp.mailfrom=alice@example.com; dkim=pass header.d=example.com",
    )
    fake = _make_fake_imap(raw)
    monkeypatch.setattr("hahobot.channels.email.imaplib.IMAP4_SSL", lambda _h, _p, **_kwargs: fake)

    cfg = _make_config(verify_dkim=True, verify_spf=True)
    channel = EmailChannel(cfg, MessageBus())
    items = channel._fetch_new_messages()

    assert len(items) == 1
    assert items[0]["sender"] == "alice@example.com"
    assert items[0]["subject"] == "Legit"


def test_email_with_partial_auth_rejected(monkeypatch) -> None:
    """An email with only spf=pass but no dkim=pass should be rejected when verify_dkim=True."""
    raw = _make_raw_email(
        subject="Partial",
        body="Only SPF passes",
        auth_results="mx.example.com; spf=pass smtp.mailfrom=alice@example.com; dkim=fail",
    )
    fake = _make_fake_imap(raw)
    monkeypatch.setattr("hahobot.channels.email.imaplib.IMAP4_SSL", lambda _h, _p, **_kwargs: fake)

    cfg = _make_config(verify_dkim=True, verify_spf=True)
    channel = EmailChannel(cfg, MessageBus())
    items = channel._fetch_new_messages()

    assert len(items) == 0, "Email with dkim=fail should be rejected"


def test_backward_compat_verify_disabled(monkeypatch) -> None:
    """When verify_dkim=False and verify_spf=False, emails without auth headers are accepted."""
    raw = _make_raw_email(subject="NoAuth", body="No auth headers present")
    fake = _make_fake_imap(raw)
    monkeypatch.setattr("hahobot.channels.email.imaplib.IMAP4_SSL", lambda _h, _p, **_kwargs: fake)

    cfg = _make_config(verify_dkim=False, verify_spf=False)
    channel = EmailChannel(cfg, MessageBus())
    items = channel._fetch_new_messages()

    assert len(items) == 1, "With verification disabled, emails should be accepted as before"


def test_email_content_tagged_with_email_context(monkeypatch) -> None:
    """Email content should be prefixed with [EMAIL-CONTEXT] for LLM isolation."""
    raw = _make_raw_email(subject="Tagged", body="Check the tag")
    fake = _make_fake_imap(raw)
    monkeypatch.setattr("hahobot.channels.email.imaplib.IMAP4_SSL", lambda _h, _p, **_kwargs: fake)

    cfg = _make_config(verify_dkim=False, verify_spf=False)
    channel = EmailChannel(cfg, MessageBus())
    items = channel._fetch_new_messages()

    assert len(items) == 1
    assert items[0]["content"].startswith("[EMAIL-CONTEXT]"), (
        "Email content must be tagged with [EMAIL-CONTEXT]"
    )


def test_check_authentication_results_method() -> None:
    """Unit test for the _check_authentication_results static method."""
    from email import policy
    from email.parser import BytesParser

    # No Authentication-Results header
    msg_no_auth = EmailMessage()
    msg_no_auth["From"] = "alice@example.com"
    msg_no_auth.set_content("test")
    parsed = BytesParser(policy=policy.default).parsebytes(msg_no_auth.as_bytes())
    spf, dkim = EmailChannel._check_authentication_results(parsed)
    assert spf is False
    assert dkim is False

    # Both pass
    msg_both = EmailMessage()
    msg_both["From"] = "alice@example.com"
    msg_both["Authentication-Results"] = (
        "mx.google.com; spf=pass smtp.mailfrom=example.com; dkim=pass header.d=example.com"
    )
    msg_both.set_content("test")
    parsed = BytesParser(policy=policy.default).parsebytes(msg_both.as_bytes())
    spf, dkim = EmailChannel._check_authentication_results(parsed)
    assert spf is True
    assert dkim is True

    # SPF pass, DKIM fail
    msg_spf_only = EmailMessage()
    msg_spf_only["From"] = "alice@example.com"
    msg_spf_only["Authentication-Results"] = (
        "mx.google.com; spf=pass smtp.mailfrom=example.com; dkim=fail"
    )
    msg_spf_only.set_content("test")
    parsed = BytesParser(policy=policy.default).parsebytes(msg_spf_only.as_bytes())
    spf, dkim = EmailChannel._check_authentication_results(parsed)
    assert spf is True
    assert dkim is False

    # DKIM pass, SPF fail
    msg_dkim_only = EmailMessage()
    msg_dkim_only["From"] = "alice@example.com"
    msg_dkim_only["Authentication-Results"] = (
        "mx.google.com; spf=fail smtp.mailfrom=example.com; dkim=pass header.d=example.com"
    )
    msg_dkim_only.set_content("test")
    parsed = BytesParser(policy=policy.default).parsebytes(msg_dkim_only.as_bytes())
    spf, dkim = EmailChannel._check_authentication_results(parsed)
    assert spf is False
    assert dkim is True


def test_authentication_results_requires_from_domain_alignment() -> None:
    msg = EmailMessage()
    msg["From"] = "allowed@example.com"
    msg["Authentication-Results"] = (
        "mx.example.net; spf=pass smtp.mailfrom=attacker@example.net; "
        "dkim=pass header.d=example.net"
    )
    msg.set_content("test")

    spf, dkim = EmailChannel._check_authentication_results(msg)

    assert spf is False
    assert dkim is False


def test_authentication_results_uses_only_nearest_header() -> None:
    msg = EmailMessage()
    msg["From"] = "allowed@example.com"
    msg["Authentication-Results"] = (
        "receiver.example; spf=fail smtp.mailfrom=example.com; dkim=fail header.d=example.com"
    )
    msg["Authentication-Results"] = (
        "forged.example; spf=pass smtp.mailfrom=example.com; dkim=pass header.d=example.com"
    )
    msg.set_content("test")

    spf, dkim = EmailChannel._check_authentication_results(msg)

    assert spf is False
    assert dkim is False


def test_authentication_results_dmarc_failure_vetoes_passes() -> None:
    msg = EmailMessage()
    msg["From"] = "allowed@example.com"
    msg["Authentication-Results"] = (
        "receiver.example; spf=pass smtp.mailfrom=example.com; "
        "dkim=pass header.d=example.com; dmarc=fail header.from=example.com"
    )
    msg.set_content("test")

    spf, dkim = EmailChannel._check_authentication_results(msg)

    assert spf is False
    assert dkim is False


def test_authentication_results_does_not_trust_result_words_in_reason_text() -> None:
    msg = EmailMessage()
    msg["From"] = "allowed@example.com"
    msg["Authentication-Results"] = (
        'receiver.example; spf=fail reason="spf=pass smtp.mailfrom=example.com"; '
        'dkim=fail reason="dkim=pass header.d=example.com"'
    )
    msg.set_content("test")

    spf, dkim = EmailChannel._check_authentication_results(msg)

    assert spf is False
    assert dkim is False


@pytest.mark.parametrize(
    "auth_results",
    [
        (
            'receiver.example; spf=fail reason="bad; spf=pass '
            'smtp.mailfrom=example.com"; dkim=fail reason="bad; dkim=pass '
            'header.d=example.com"'
        ),
        (
            'receiver.example; spf=pass reason="reported smtp.mailfrom=example.com" '
            'smtp.mailfrom=attacker.net; dkim=pass reason="reported header.d=example.com" '
            "header.d=attacker.net"
        ),
        (
            "receiver.example; spf=pass (outer (reported smtp.mailfrom=example.com)) "
            "smtp.mailfrom=attacker.net; dkim=pass (reported header.d=example.com) "
            "header.d=attacker.net"
        ),
        ("receiver.example; spf=pass smtp.mailfrom=co.uk; dkim=pass header.d=co.uk"),
    ],
)
def test_authentication_results_ignores_nested_or_ambiguous_fields(
    auth_results: str,
) -> None:
    msg = EmailMessage()
    msg["From"] = "allowed@example.com" if "co.uk" not in auth_results else "allowed@victim.co.uk"
    msg["Authentication-Results"] = auth_results
    msg.set_content("test")

    assert EmailChannel._check_authentication_results(msg) == (False, False)


def test_authentication_results_accepts_top_level_pairs_with_comments() -> None:
    msg = EmailMessage()
    msg["From"] = "allowed@example.com"
    msg["Authentication-Results"] = (
        "receiver.example; spf (method) / (version) 1 = (result) pass "
        "smtp (property) . mailfrom=(identity)example.com; "
        "dkim=(result)pass header (property) . d=example.com"
    )
    msg.set_content("test")

    assert EmailChannel._check_authentication_results(msg) == (True, True)


def test_authentication_results_rejects_duplicate_identity_properties() -> None:
    msg = EmailMessage()
    msg["From"] = "allowed@example.com"
    msg["Authentication-Results"] = (
        "receiver.example; spf=pass smtp.mailfrom=example.com "
        "smtp.mailfrom=attacker.net; dkim=pass header.d=example.com header.d=attacker.net"
    )
    msg.set_content("test")

    assert EmailChannel._check_authentication_results(msg) == (False, False)


@pytest.mark.parametrize(
    "auth_results",
    [
        (
            "receiver.example; spf=pass spf=fail smtp.mailfrom=example.com; "
            "dkim=pass header.d=example.com"
        ),
        (
            "receiver.example; dmarc=pass dmarc=fail; "
            "spf=pass smtp.mailfrom=example.com; dkim=pass header.d=example.com"
        ),
    ],
)
def test_authentication_results_rejects_repeated_method_results(auth_results: str) -> None:
    msg = EmailMessage()
    msg["From"] = "allowed@example.com"
    msg["Authentication-Results"] = auth_results
    msg.set_content("test")

    assert EmailChannel._check_authentication_results(msg) == (False, False)


def test_authentication_results_allows_apostrophe_in_unquoted_reason() -> None:
    msg = EmailMessage()
    msg["From"] = "allowed@example.com"
    msg["Authentication-Results"] = (
        "receiver.example; spf=pass reason=sender's-policy smtp.mailfrom=example.com; "
        "dkim=pass header.d=example.com"
    )
    msg.set_content("test")

    assert EmailChannel._check_authentication_results(msg) == (True, True)


@pytest.mark.parametrize(
    "from_addr",
    ["victim user@example.com", "victim..x@example.com"],
)
def test_parse_from_address_rejects_header_defects(from_addr: str) -> None:
    message = EmailMessage()
    message["From"] = from_addr

    assert EmailChannel._parse_from_address(message) == ""


def test_parse_from_address_preserves_quoted_local_part() -> None:
    message = EmailMessage()
    message["From"] = '"a@b"@example.com'

    assert EmailChannel._parse_from_address(message) == '"a@b"@example.com'


def test_parse_from_address_rejects_duplicate_fields() -> None:
    from email import policy
    from email.parser import BytesParser

    message = BytesParser(policy=policy.default).parsebytes(
        b"From: allowed@example.com\r\n"
        b"From: attacker@example.net\r\n"
        b"Authentication-Results: receiver.example; "
        b"spf=pass smtp.mailfrom=example.com; dkim=pass header.d=example.com\r\n\r\n"
    )

    assert EmailChannel._parse_from_address(message) == ""
    assert EmailChannel._check_authentication_results(message) == (False, False)


def test_parse_from_address_rejects_named_group() -> None:
    message = EmailMessage()
    message["From"] = "Friends: allowed@example.com;"

    assert EmailChannel._parse_from_address(message) == ""


# ---------------------------------------------------------------------------
# Attachment extraction tests
# ---------------------------------------------------------------------------


def _make_raw_email_with_attachment(
    from_addr: str = "alice@example.com",
    subject: str = "With attachment",
    body: str = "See attached.",
    attachment_name: str = "doc.pdf",
    attachment_content: bytes = b"%PDF-1.4 fake pdf content",
    attachment_mime: str = "application/pdf",
    auth_results: str | None = None,
) -> bytes:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = "bot@example.com"
    msg["Subject"] = subject
    msg["Message-ID"] = "<m1@example.com>"
    if auth_results:
        msg["Authentication-Results"] = auth_results
    msg.set_content(body)
    maintype, subtype = attachment_mime.split("/", 1)
    msg.add_attachment(
        attachment_content,
        maintype=maintype,
        subtype=subtype,
        filename=attachment_name,
    )
    return msg.as_bytes()


def test_extract_attachments_saves_pdf(tmp_path, monkeypatch) -> None:
    """PDF attachment is saved to media dir and path returned in media list."""
    monkeypatch.setattr("hahobot.channels.email.get_media_dir", lambda ch: tmp_path)

    raw = _make_raw_email_with_attachment()
    fake = _make_fake_imap(raw)
    monkeypatch.setattr("hahobot.channels.email.imaplib.IMAP4_SSL", lambda _h, _p, **_kwargs: fake)

    cfg = _make_config(
        allowed_attachment_types=["application/pdf"], verify_dkim=False, verify_spf=False
    )
    channel = EmailChannel(cfg, MessageBus())
    items = channel._fetch_new_messages()

    assert len(items) == 1
    assert len(items[0]["media"]) == 1
    saved_path = Path(items[0]["media"][0])
    assert saved_path.exists()
    assert saved_path.read_bytes() == b"%PDF-1.4 fake pdf content"
    assert "500_doc.pdf" in saved_path.name
    assert "[attachment:" in items[0]["content"]


def test_extract_attachments_disabled_by_default(monkeypatch) -> None:
    """With no allowed_attachment_types (default), no attachments are extracted."""
    raw = _make_raw_email_with_attachment()
    fake = _make_fake_imap(raw)
    monkeypatch.setattr("hahobot.channels.email.imaplib.IMAP4_SSL", lambda _h, _p, **_kwargs: fake)

    cfg = _make_config(verify_dkim=False, verify_spf=False)
    assert cfg.allowed_attachment_types == []
    channel = EmailChannel(cfg, MessageBus())
    items = channel._fetch_new_messages()

    assert len(items) == 1
    assert items[0]["media"] == []
    assert "[attachment:" not in items[0]["content"]


def test_extract_attachments_mime_type_filter(tmp_path, monkeypatch) -> None:
    """Non-allowed MIME types are skipped."""
    monkeypatch.setattr("hahobot.channels.email.get_media_dir", lambda ch: tmp_path)

    raw = _make_raw_email_with_attachment(
        attachment_name="image.png",
        attachment_content=b"\x89PNG fake",
        attachment_mime="image/png",
    )
    fake = _make_fake_imap(raw)
    monkeypatch.setattr("hahobot.channels.email.imaplib.IMAP4_SSL", lambda _h, _p, **_kwargs: fake)

    cfg = _make_config(
        allowed_attachment_types=["application/pdf"],
        verify_dkim=False,
        verify_spf=False,
    )
    channel = EmailChannel(cfg, MessageBus())
    items = channel._fetch_new_messages()

    assert len(items) == 1
    assert items[0]["media"] == []


def test_extract_attachments_empty_allowed_types_rejects_all(tmp_path, monkeypatch) -> None:
    """Empty allowed_attachment_types means no types are accepted."""
    monkeypatch.setattr("hahobot.channels.email.get_media_dir", lambda ch: tmp_path)

    raw = _make_raw_email_with_attachment(
        attachment_name="image.png",
        attachment_content=b"\x89PNG fake",
        attachment_mime="image/png",
    )
    fake = _make_fake_imap(raw)
    monkeypatch.setattr("hahobot.channels.email.imaplib.IMAP4_SSL", lambda _h, _p, **_kwargs: fake)

    cfg = _make_config(
        allowed_attachment_types=[],
        verify_dkim=False,
        verify_spf=False,
    )
    channel = EmailChannel(cfg, MessageBus())
    items = channel._fetch_new_messages()

    assert len(items) == 1
    assert items[0]["media"] == []


def test_extract_attachments_wildcard_pattern(tmp_path, monkeypatch) -> None:
    """Glob patterns like 'image/*' match attachment MIME types."""
    monkeypatch.setattr("hahobot.channels.email.get_media_dir", lambda ch: tmp_path)

    raw = _make_raw_email_with_attachment(
        attachment_name="photo.jpg",
        attachment_content=b"\xff\xd8\xff fake jpeg",
        attachment_mime="image/jpeg",
    )
    fake = _make_fake_imap(raw)
    monkeypatch.setattr("hahobot.channels.email.imaplib.IMAP4_SSL", lambda _h, _p, **_kwargs: fake)

    cfg = _make_config(
        allowed_attachment_types=["image/*"],
        verify_dkim=False,
        verify_spf=False,
    )
    channel = EmailChannel(cfg, MessageBus())
    items = channel._fetch_new_messages()

    assert len(items) == 1
    assert len(items[0]["media"]) == 1


def test_extract_attachments_size_limit(tmp_path, monkeypatch) -> None:
    """Attachments exceeding max_attachment_size are skipped."""
    monkeypatch.setattr("hahobot.channels.email.get_media_dir", lambda ch: tmp_path)

    raw = _make_raw_email_with_attachment(
        attachment_content=b"x" * 1000,
    )
    fake = _make_fake_imap(raw)
    monkeypatch.setattr("hahobot.channels.email.imaplib.IMAP4_SSL", lambda _h, _p, **_kwargs: fake)

    cfg = _make_config(
        allowed_attachment_types=["*"],
        max_attachment_size=500,
        verify_dkim=False,
        verify_spf=False,
    )
    channel = EmailChannel(cfg, MessageBus())
    items = channel._fetch_new_messages()

    assert len(items) == 1
    assert items[0]["media"] == []


def test_extract_attachments_max_count(tmp_path, monkeypatch) -> None:
    """Only max_attachments_per_email are saved."""
    monkeypatch.setattr("hahobot.channels.email.get_media_dir", lambda ch: tmp_path)

    # Build email with 3 attachments
    msg = EmailMessage()
    msg["From"] = "alice@example.com"
    msg["To"] = "bot@example.com"
    msg["Subject"] = "Many attachments"
    msg["Message-ID"] = "<m1@example.com>"
    msg.set_content("See attached.")
    for i in range(3):
        msg.add_attachment(
            f"content {i}".encode(),
            maintype="application",
            subtype="pdf",
            filename=f"doc{i}.pdf",
        )
    raw = msg.as_bytes()

    fake = _make_fake_imap(raw)
    monkeypatch.setattr("hahobot.channels.email.imaplib.IMAP4_SSL", lambda _h, _p, **_kwargs: fake)

    cfg = _make_config(
        allowed_attachment_types=["*"],
        max_attachments_per_email=2,
        verify_dkim=False,
        verify_spf=False,
    )
    channel = EmailChannel(cfg, MessageBus())
    items = channel._fetch_new_messages()

    assert len(items) == 1
    assert len(items[0]["media"]) == 2


def test_extract_attachments_sanitizes_filename(tmp_path, monkeypatch) -> None:
    """Path traversal in filenames is neutralized."""
    monkeypatch.setattr("hahobot.channels.email.get_media_dir", lambda ch: tmp_path)

    raw = _make_raw_email_with_attachment(
        attachment_name="../../../etc/passwd",
    )
    fake = _make_fake_imap(raw)
    monkeypatch.setattr("hahobot.channels.email.imaplib.IMAP4_SSL", lambda _h, _p, **_kwargs: fake)

    cfg = _make_config(allowed_attachment_types=["*"], verify_dkim=False, verify_spf=False)
    channel = EmailChannel(cfg, MessageBus())
    items = channel._fetch_new_messages()

    assert len(items) == 1
    assert len(items[0]["media"]) == 1
    saved_path = Path(items[0]["media"][0])
    # File must be inside the media dir, not escaped via path traversal
    assert saved_path.parent == tmp_path
