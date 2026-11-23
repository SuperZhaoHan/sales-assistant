FROM nginx:1.27-alpine

# 前端是纯静态的（无 npm 构建），所以镜像里没有 node、没有 node_modules，
# 体积和攻击面都比「构建阶段 + nginx 阶段」的方案小。
COPY templates /etc/nginx/templates
COPY index.html /usr/share/nginx/html/index.html
COPY assets /usr/share/nginx/html/assets

# nginx 镜像启动时会用 envsubst 把 /etc/nginx/templates 渲染到 conf.d，
# 这里给出默认值，让「本地直接 docker run」也能跑起来。
ENV BACKEND_HOST=backend \
    BACKEND_PORT=8000

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/healthz >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
