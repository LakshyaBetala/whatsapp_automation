"""Backward-compatible shim. The public site now lives in app/site.py as a
multi-page, SEO-first site. This module keeps the old import working
(build_zip's static landing zip imports landing_html) by re-exporting the
home page. Edit content and design in app/site.py.
"""
from __future__ import annotations

from app.site import CONTACT_EMAIL, CONTACT_WA, landing_html

__all__ = ["landing_html", "CONTACT_WA", "CONTACT_EMAIL"]
