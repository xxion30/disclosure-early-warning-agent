version: v1
score_result.breakdown의 각 항목을 한 번씩 설명한다. items의 breakdown_index는 원래 배열의 0부터 시작하는 인덱스다.
설명은 입력 점수·등급을 바꾸지 않으며 각 항목에 주어진 evidence를 연결한다. 다른 항목의 근거로 대신하지 않는다.
시간 경과, 기본 점수, 조합 상향은 제공된 계산 내역에 있을 때만 설명한다.
각 evidence의 source, quote, rcept_no, location은 입력에서 가져온다. 원문과 규칙 해석, 불확실성을 구분한다.
