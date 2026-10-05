from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string


def send_templated_email(*, to: str, template: str, context: dict) -> None:
    """
    Sends a text + HTML email from three templates:
    `<template>_subject.txt`, `<template>.txt` and `<template>.html`.
    Raises on failure; callers decide how to handle it.
    """
    # One line only: a stray newline in a subject is a header-injection vector.
    subject = " ".join(render_to_string(f"{template}_subject.txt", context).split())
    message = EmailMultiAlternatives(
        subject=subject,
        body=render_to_string(f"{template}.txt", context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to],
    )
    message.attach_alternative(render_to_string(f"{template}.html", context), "text/html")
    message.send(fail_silently=False)
