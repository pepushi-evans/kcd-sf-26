# One image, two roles: the checkout web service (default) and the load
# generator (command: python /app/load.py). Built locally and side-loaded
# into kind, so the demo works on any CPU architecture with no registry.
FROM python:3.12-alpine
WORKDIR /app
COPY server.py load.py /app/
USER 65532:65532
ENV PORT=9898
EXPOSE 9898
CMD ["python", "/app/server.py"]
