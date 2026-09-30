"""Official Python client for the MailRambo email verification API.

    from mailrambo import MailRambo

    mr = MailRambo()  # reads MAILRAMBO_API_KEY
    result = mr.verify("jane@acme.com")
    if not result["deliverable"]:
        print("Don't send:", result["reason"])

Docs: https://www.mailrambo.com/developers
"""

from ._client import AsyncMailRambo, MailRambo, MailRamboError

__all__ = ["MailRambo", "AsyncMailRambo", "MailRamboError"]
__version__ = "0.1.0"
