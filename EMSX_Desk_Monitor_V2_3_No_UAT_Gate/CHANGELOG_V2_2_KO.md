# V2.2 변경 사항

- OMS API 시퀀스 검사에서 heartbeat (status 1) 및 initial paint end (status 11) 이벤트에
  `API_SEQ_NUM`이 존재할 경우 이를 반영하도록 변경했습니다.
- worker와 GUI의 시퀀스 계산을 일치시켰습니다.
- 시퀀스 불연속/역행 경고에 `EVENT_STATUS`를 기록하여 원인 추적을 쉽게 했습니다.
- `V2.1` 대비 ACK 적격성, 계좌 범위, 승인 플래그, 주문 요청 로직은 변경하지 않았습니다.
- 6개의 시퀀스 regression test를 추가했습니다.
