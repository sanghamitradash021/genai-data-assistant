# Authentication Notes

## How authentication works

Users sign in with an email and password. The server checks the password hash and, on success, issues a
signed JWT access token valid for 15 minutes plus a refresh token valid for 7 days.

## Session storage

Refresh tokens are stored in an HTTP-only cookie. Access tokens are kept in memory on the client and are
never written to local storage.

## Password resets

A user who forgets their password requests a reset link by email. The link contains a one-time token that
expires after 30 minutes.
