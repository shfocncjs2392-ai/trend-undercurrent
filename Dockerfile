# 1. 가볍고 안정적인 파이썬 베이스
FROM python:3.10-slim

WORKDIR /app

# 2. 필수 빌드 도구 및 크롬(Selenium) 종속성 설치 (yum groupinstall "Development Tools" 대체)
RUN apt-get update && apt-get install -y \
    wget curl git make gcc g++ autoconf automake libtool \
    fonts-liberation libappindicator3-1 libasound2 libatk-bridge2.0-0 \
    libatk1.0-0 libcups2 libdbus-1-3 libnspr4 libnss3 \
    libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2 xdg-utils \
    # 데비안 최신 버전 호환 패키지 추가
    libgdk-pixbuf-2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 3. 크롬 브라우저 설치
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list' \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# 4. Mecab-ko (엔진) 소스 컴파일
RUN cd /tmp && \
    wget https://bitbucket.org/eunjeon/mecab-ko/downloads/mecab-0.996-ko-0.9.2.tar.gz && \
    tar xvfz mecab-0.996-ko-0.9.2.tar.gz && \
    cd mecab-0.996-ko-0.9.2 && \
    ./configure && make && make install && \
    ldconfig

# 5. Mecab-ko-dic (사전) 다운로드 및 기본 컴파일
RUN cd /tmp && \
    wget https://bitbucket.org/eunjeon/mecab-ko-dic/downloads/mecab-ko-dic-2.1.1-20180720.tar.gz && \
    tar xvfz mecab-ko-dic-2.1.1-20180720.tar.gz && \
    cd mecab-ko-dic-2.1.1-20180720 && \
    ./autogen.sh && ./configure && make

# 6. 신조어 커스텀 사전 주입 및 최종 설치 (핵심 포인트!)
COPY trend_words.csv /tmp/mecab-ko-dic-2.1.1-20180720/user-dic/
RUN cd /tmp/mecab-ko-dic-2.1.1-20180720 && \
    ./tools/add-userdic.sh && \
    make install

# 7. 파이썬 환경 세팅 및 코드 복사
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 8. 포트 개방 및 앱 실행
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
