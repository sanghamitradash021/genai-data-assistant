FROM python:3.11-slim
ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
WORKDIR /ui
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app.py .
RUN useradd --create-home --uid 1001 ui && chown -R ui /ui
USER ui
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--browser.gatherUsageStats=false"]
