FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 transport

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY xmpp_transport_telegram ./xmpp_transport_telegram
COPY docker/entrypoint.sh /usr/local/bin/transport-entrypoint

RUN chmod +x /usr/local/bin/transport-entrypoint \
    && mkdir -p /app/logs /app/run /app/data/telegram_sessions \
    && chown -R transport:transport /app

EXPOSE 8089

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8089/health', timeout=3).read()"

ENTRYPOINT ["transport-entrypoint"]
CMD ["python", "-m", "xmpp_transport_telegram"]
