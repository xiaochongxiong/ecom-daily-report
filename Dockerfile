FROM python:3.12-slim

WORKDIR /app

# 时区设为东八区，否则定时任务和报表时间会错 8 小时
# slim 镜像不带 tzdata，必须显式安装，ENV TZ 才会生效
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt psycopg[binary]

COPY . .

CMD ["python", "run_once.py"]
