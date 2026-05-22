FROM node:20-alpine

WORKDIR /opt/verification

COPY server.mjs ./server.mjs
COPY core.mjs ./core.mjs

EXPOSE 4010

ENTRYPOINT ["node", "/opt/verification/server.mjs"]
