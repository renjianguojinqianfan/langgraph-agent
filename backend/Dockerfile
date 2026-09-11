FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
# 不再内置 .env（早期版本复制 .env.example，会带入公开的 auth_token=changeme 占位值）。
# 配置一律由环境变量 / docker-compose 注入，代码默认值兜底；main.py 启动时会告警不安全默认值。

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
