# Joe Electronics — local SQLite booking system

This site now runs as a small Python standard-library web server with a persistent SQLite database. It does not need Supabase, API keys, Node, or third-party Python packages.

## Run it on Windows

1. Open a PowerShell terminal in this `electronics` folder.
2. Start the server:

   `py server.py`

   If `py` is not available, use the selected Python executable from VS Code. The server listens at `http://127.0.0.1:8000/` by default.
3. Open `http://127.0.0.1:8000/` in the browser. Do not open `index.html` using a `file://` URL; browser pages must use the same-origin server for the API and secure session cookie.
4. The database is created automatically outside the web folder at `%USERPROFILE%\.joe-electronics\store.sqlite3`, and starter products are inserted on first run.

Stop the server with Ctrl+C. The SQLite file remains for the next run. Back it up by stopping the server and copying the database file.

## Create the first admin

Run this once from the project directory:

`py server.py create-admin admin@example.com`

The command prompts for a password without echoing it and creates or promotes that account to the `admin` role. Only one admin can exist at a time. Then start the server, log in at `login.html`, and open `admin.html`.

There is no public admin signup option. Admin product and booking operations are checked on the server, not just hidden in the page. Once signed in, the current admin can use **Transfer admin access** at the bottom of the dashboard to hand ownership to an existing customer account. The handoff requires the current password, the successor's registered email, and explicit confirmation; it signs the former admin out. The bootstrap command refuses to create a second admin after an owner exists.

If an older database already contains multiple admin users, startup keeps the oldest admin account by database ID and changes the others to customer accounts. Future admin handoffs must use the dashboard.

## How bookings work

- Sign up or log in before booking a product.
- Prices are read from the database at booking time; customers cannot submit a different price or another user's ID.
- A booking is a request for follow-up, not a paid order. The admin sees the contact and delivery details and can update the request status.
- Customer booking lists are filtered by the signed-in account on the server. Passwords are stored as salted scrypt hashes; auth uses a random HttpOnly, SameSite cookie.

## Local-development security boundary

The default bind address is `127.0.0.1`, so this is intended for running locally on the same PC. Do not expose the development server to the public internet. A public deployment should use HTTPS, a production WSGI/server setup, secure cookies, backups, and appropriate account-recovery/monitoring. The server supports `--secure-cookie` when it is behind HTTPS.
