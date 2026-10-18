# Security policy

## Reporting a vulnerability

Please report security problems privately through GitHub:
**Security → Report a vulnerability** on this repository. Do not open a public issue.

Include what you found, how to reproduce it (a small crafted evidence file is ideal) and the
version you tested. You will get an acknowledgement within a week.

## Scope

Connected Phone Forensics reads evidence that may have been prepared by an adversary. These are
in scope:

- a crafted file (registry hive, SQLite database, plist, log) that crashes the tool, makes it hang
  or exhaust memory, or makes it write outside its output folder
- a crafted file that makes the tool report something false without a warning (for example a
  record attributed to the wrong phone)
- anything that modifies the evidence being read

## Supported versions

Security fixes are made for the latest release.
