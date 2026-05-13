#!/bin/sh
set -e
mkdir -p /etc/nginx/certs
if [ ! -f /etc/nginx/certs/server.crt ]; then
  openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout /etc/nginx/certs/server.key \
    -out /etc/nginx/certs/server.crt \
    -subj "/CN=localhost/O=dev/C=US"
  chmod 644 /etc/nginx/certs/server.crt
  chmod 600 /etc/nginx/certs/server.key
fi
