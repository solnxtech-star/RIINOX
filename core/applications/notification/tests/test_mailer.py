"""How notification emails leave the server (settings.NOTIFICATION_EMAIL_MODE)."""

from __future__ import annotations

from unittest import mock

from django.core import mail
from django.test import SimpleTestCase
from django.test import override_settings

from core.applications.notification import mailer

CONTEXT = {"organization_name": "Okafor Stores", "title": "Low Stock", "message": "Coca-Cola is low.",
           "location_name": "Lekki", "url": "", "event_label": "Low Stock"}


class MailerTests(SimpleTestCase):
    def setUp(self):
        mail.outbox.clear()

    @override_settings(NOTIFICATION_EMAIL_MODE="sync")
    def test_sync_sends_one_email_per_recipient(self):
        mailer.send_notification_emails(["a@x.test", "b@x.test"], CONTEXT)
        self.assertEqual([m.to for m in mail.outbox], [["a@x.test"], ["b@x.test"]])
        self.assertEqual(mail.outbox[0].subject, "[Okafor Stores] Low Stock")

    def test_one_failure_does_not_stop_the_others(self):
        real = mailer.send_templated_email

        def flaky(*, to, **kwargs):
            if to == "bad@x.test":
                raise ConnectionError("provider down")
            return real(to=to, **kwargs)

        with mock.patch.object(mailer, "send_templated_email", side_effect=flaky):
            sent = mailer.send_now(["bad@x.test", "good@x.test"], CONTEXT)
        self.assertEqual(sent, 1)
        self.assertEqual([m.to for m in mail.outbox], [["good@x.test"]])

    @override_settings(NOTIFICATION_EMAIL_MODE="thread")
    def test_thread_mode_sends_without_a_worker(self):
        with mock.patch.object(mailer._pool, "submit") as submit:
            mailer.send_notification_emails(["a@x.test"], CONTEXT)
        submit.assert_called_once_with(mailer.send_now, ["a@x.test"], CONTEXT)

    @override_settings(NOTIFICATION_EMAIL_MODE="celery")
    def test_celery_mode_falls_back_to_direct_send_when_the_broker_is_down(self):
        with mock.patch("core.applications.notification.tasks.send_notification_emails.delay",
                        side_effect=ConnectionError("no broker")), \
                mock.patch.object(mailer._pool, "submit") as submit:
            mailer.send_notification_emails(["a@x.test"], CONTEXT)
        submit.assert_called_once()
