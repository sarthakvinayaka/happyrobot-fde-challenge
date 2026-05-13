FROM nginx:alpine

RUN apk add --no-cache openssl

COPY nginx.conf /etc/nginx/nginx.conf
COPY docker-entrypoint.d/99-gen-certs.sh /docker-entrypoint.d/99-gen-certs.sh
RUN chmod +x /docker-entrypoint.d/99-gen-certs.sh

EXPOSE 80 443
