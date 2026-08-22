FROM apache/superset:4.1.1

USER root
RUN pip install --no-cache-dir clickhouse-connect==0.8.11 psycopg2-binary==2.9.10
COPY superset_config.py /app/pythonpath/superset_config.py
COPY bootstrap.sh /app/bootstrap.sh
RUN chmod +x /app/bootstrap.sh
USER superset

CMD ["/app/bootstrap.sh"]
