FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p log data

COPY biz ./biz
COPY conf ./conf
COPY api.py .
COPY run_branch_commit_review.py .

EXPOSE 5001

CMD ["python", "api.py"]