FROM alpine:3.21

RUN apk add --no-cache python3 \
    && addgroup -g 10001 -S sandbox \
    && adduser -u 10001 -S -G sandbox -h /nonexistent -s /sbin/nologin sandbox

WORKDIR /app
COPY main.py /app/main.py
RUN chmod 0555 /app/main.py

USER 10001:10001
ENTRYPOINT ["python3", "/app/main.py"]
