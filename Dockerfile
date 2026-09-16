FROM python:3.12-alpine

RUN addgroup -S monitor && adduser -S -G monitor monitor
WORKDIR /app
COPY --chown=monitor:monitor app.py /app/app.py
RUN mkdir -p /data && chown monitor:monitor /data

USER monitor
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"
CMD ["python", "/app/app.py"]
