ARG PILOT_BASE_IMAGE=argus-web-trial:reuse-d6c17f8e7b-20260912
FROM ${PILOT_BASE_IMAGE}
USER root
RUN python -m pip install --no-cache-dir numpy==1.26.4 pandas==2.2.3 openpyxl==3.1.5 pdfplumber==0.11.9 pypdf==6.6.0 reportlab==4.4.7 rapidfuzz==3.14.3 pytest==8.4.1 pytest-json-ctrf==0.3.6
WORKDIR /root
ENTRYPOINT []
CMD ["sleep", "infinity"]
