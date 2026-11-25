# TinyShop

TinyShop is a deliberately small online-shop backend used as a test fixture for RepoChat.

## Setup

Install Python 3.12, then run `python -m shop.db` to create the SQLite database file `shop.db`.

## Architecture

- `shop/payments.py` charges cards and issues refunds through a fake payment gateway.
- `shop/db.py` stores orders in SQLite.
- `web/cart.ts` is the browser-side shopping cart.
- `api/Server.java` is the HTTP server.
- `worker/queue.go` processes background jobs such as sending receipts.
