"""Demo mailbox: shows only the e-mails sent to the address asked for, never everyone's."""
from app.services.email import send_email


def test_mailbox_shows_only_one_persons_mail(client):
    send_email("first.student@university.edu", "Verify your e-mail", "Link for the first student")
    send_email("Second.Student@university.edu", "Verify your e-mail", "Link for the second student")
    assert client.get("/api/dev/outbox").json() == []  # no address: nothing listed
    mine = client.get("/api/dev/outbox?to=second.student@university.edu").json()
    assert len(mine) == 1 and "second student" in mine[0]["body"]  # case-insensitive, nobody else's mail
    assert client.get("/api/dev/outbox?to=nobody@university.edu").json() == []
