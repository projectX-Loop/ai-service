---
source_id: concept/rebalancing
title: 리밸런싱
source_url: https://app.notion.com/p/3cfea37df5ce80979260ec4b9b2f4246
published_at: 2026-09-02
license: 팀 내부 문서 (KAN-9 v0.2 §4.2 리밸런싱 규칙). 자체 작성. 용어 출처 TODO(성종현)
concept_tags: rebalancing, target_weights, max_drift_pct
---

## 정의

리밸런싱은 가격 변동으로 달라진 자산 비중을 처음 정한 목표 비중으로 되돌리는 일입니다. 오른 자산을 일부 팔고 내린 자산을 사서 비중을 맞춥니다.

## 주기별 차이

월별·분기별·반기별로 되돌리는 시점이 다릅니다. 자주 맞출수록 목표 비중에서 덜 벗어나지만 매매가 늘어 거래 비용이 커지고, 드물게 맞출수록 비용은 줄지만 비중이 더 크게 벗어날 수 있습니다.

## 이 서비스에서

세 주기를 모두 계산해 비교합니다. 어느 주기가 낫다고 정하지 않고, 각 주기에서 관찰된 결과와 그에 따르는 대가를 나란히 보여줍니다.
