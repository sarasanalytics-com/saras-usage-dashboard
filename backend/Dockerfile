FROM node:18-slim

WORKDIR /app

COPY package*.json ./

RUN npm install --only=production

COPY app.js .

EXPOSE 8080

CMD ["node", "app.js"]
