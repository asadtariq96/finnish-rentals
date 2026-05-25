"""Shared Finnish-rental text classifiers used by vuokraovi.py and oikotie.py.

The regexes target free-text descriptions on Finnish rental sites.
Verified against ~470 real listings via LLM (May 2026), zero false positives.
See: project-vuokraovi-api and project-oikotie-api memories for the discovery story.
"""
import re

# Parking hall / garage. Anchors on Finnish stems (case-form proof), with English equivalents.
PARK_GARAGE_RE = re.compile(
    r"(pysäköintihalli|"
    r"pysäköintilaito|"               # -laitos, -laitoksessa
    r"pysäköintirakennu|"             # -rakennus
    r"pysäköintitalo|"                # parking building (synonym of parkkitalo)
    r"paikoitushalli|"                # synonym of pysäköintihalli
    r"autohalli|autotall[ie]|"        # autotalli, autotalleja (plural)
    r"parkkihalli|parkkitalo|"
    r"autopaik[a-zäö]*\s+(?:pihakannen|kannen)\s+al|"
    r"(?:pihakannen|kannen)\s+ala[a-zäö]*\s+(?:hall|paikoi|pysäköi)|"
    r"maanalai[a-zäö]*\s+(?:pysäköi|autopaik|halli|rakennu)|"
    r"parking\s*hall|parking\s*garage|underground\s*park|"
    r"parking\s+space.{0,80}(?:stairwell|elevator|underground|garage|indoor))",
    re.I,
)

# EV charging. Finnish verb root is `lata-`; `latau-` (noun forms) and `lataa-`/`lataam-` both common.
EV_CHARGE_RE = re.compile(
    r"(sähköauto[a-zäö]*\s*lata[au]|"           # sähköauton lataaminen, latauspiste
    r"sähköautolata[au]|"
    r"latauspist|latauspaik|lataustolp|"
    r"latauslaite|"                              # charging device
    r"latausmahdollisuu|"
    r"\d+\s*k[Ww]\s*lata[au]|"                  # "11 kW latauksella"
    r"EV[\s-]?charg|electric\s+vehicle.{0,30}charg)",
    re.I,
)


def has_parking_garage(text):
    return bool(PARK_GARAGE_RE.search(text or ""))


def has_ev_charging(text):
    return bool(EV_CHARGE_RE.search(text or ""))
