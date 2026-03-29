FROM mem0/mem0-api-server:latest
RUN pip install \
    "psycopg[binary,pool]" \
    psycopg2-binary \
    neo4j \
    langchain-neo4j \
    rank-bm25 \
    langchain-community
RUN mkdir -p /app/history
