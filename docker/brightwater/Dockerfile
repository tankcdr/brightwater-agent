# The brightwater package, for the services that run its code in a container:
# the AIP server (Ch 4 onward). AIP 0.7.0 requires Python 3.14.
FROM python:3.14-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
ENTRYPOINT ["aip"]
