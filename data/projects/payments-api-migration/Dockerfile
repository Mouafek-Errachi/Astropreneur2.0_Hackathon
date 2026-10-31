FROM python:3.12-slim
COPY requirements.txt .
RUN pip install -r requirements.txt
CMD ["uvicorn", "app:app", "--host", "0.0.0.0"]
