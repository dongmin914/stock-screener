# Stock Screener

나의 진입 체크리스트(볼밴/일목/RSI/거래량/이격도)를 만족하는 종목을 매일 자동 스캔하는 대시보드.

**대상 시장**: 미국 (S&P 500 + NASDAQ-100)
**실행**: 매일 1회 (장마감 후)

## Entry Checklist

1. 현재가 < 200일 이동평균선
2. 200일선 대비 이격도 -10% 이하
3. RSI(14) 30 이하를 최근 경험 후 반등 중
4. 볼린저밴드 하단(20, 2σ) 터치 또는 이탈 후 복귀
5. 최근 거래량이 20일 평균의 1.5배 이상 + 양봉

3개 이상 충족 시 분할 매수 후보, 5개 충족 시 본진입 후보.

## Local Run

```bash
pip install -r requirements.txt
python -m src.run               # 배치 실행, data/results.csv 생성
streamlit run app.py            # 대시보드 실행
```

## Deploy

- **Dashboard**: Streamlit Community Cloud (repo URL 연결)
- **Daily batch**: GitHub Actions cron (`.github/workflows/daily-scan.yml`)
