#!/usr/bin/env bash
# End-to-end happy path for posts, outbox fanout, and mixed feed.
set -euo pipefail

API=${API:-http://localhost:8000}
step() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
json() { curl -sS --fail-with-body -H 'Content-Type: application/json' "$@"; }
auth_json() { curl -sS --fail-with-body -H 'Content-Type: application/json' -H "Authorization: Bearer $1" "${@:2}"; }
assert_eq() {
  if [[ "$1" != "$2" ]]; then
    printf 'Assertion failed: %s (expected %s, got %s)\n' "$3" "$2" "$1" >&2
    exit 1
  fi
  printf '✓ %s: %s\n' "$3" "$1"
}

step "Создать 20 пользователей и получить токены"
RUN_ID="$(date +%s%N)-$RANDOM"
declare -a IDS TOKENS
for i in $(seq 0 19); do
  creds=$(jq -nc --arg email "demo-${RUN_ID}-${i}@example.com" --arg p "DemoPass-${RUN_ID}-Aa9!" '{email:$email,password:$p}')
  user=$(json -X POST "$API/api/v1/auth/register" -d "$creds")
  IDS[$i]=$(jq -er .id <<<"$user")
  TOKENS[$i]=$(json -X POST "$API/api/v1/auth/token" -d "$creds" | jq -er .access_token)
done
printf 'Созданы пользователи: %s … %s\n' "${IDS[0]}" "${IDS[19]}"
RECIPIENT=${IDS[0]}
NONCELEB=${IDS[18]}
CELEB=${IDS[19]}

step "Подписать 19 пользователей на celeb; у обычного автора один подписчик"
for i in $(seq 0 18); do
  auth_json "${TOKENS[$i]}" -X POST "$API/api/v1/users/$CELEB/followers" >/dev/null
done
auth_json "${TOKENS[0]}" -X POST "$API/api/v1/users/$NONCELEB/followers" >/dev/null
follower_count=$(auth_json "${TOKENS[0]}" "$API/api/v1/users/$CELEB/followers" | jq 'length')
assert_eq "$follower_count" 19 'число подписчиков celeb'

step "Загрузить через постовую ручку 100 celeb и 20 обычных постов"
TMP_IMAGE=$(mktemp --suffix=.jpg)
trap 'rm -f "$TMP_IMAGE"' EXIT
# Tiny valid JPEG payload is sufficient for RustFS; delivery is outside the demo.
printf '\377\330\377\331' >"$TMP_IMAGE"
declare -a CELEB_POSTS NONCELEB_POSTS
create_post() {
  local token=$1 text=$2
  curl -sS --fail-with-body -H "Authorization: Bearer $token" \
    -F "file=@$TMP_IMAGE;type=image/jpeg" -F "text=$text" "$API/api/v1/posts" | jq -er .id
}
for i in $(seq 1 20); do
  NONCELEB_POSTS+=("$(create_post "${TOKENS[18]}" "ordinary-$i")")
done
for i in $(seq 1 100); do
  CELEB_POSTS+=("$(create_post "${TOKENS[19]}" "celeb-$i")")
done

step "Дождаться, пока outbox опубликует все 120 постов"
wait_published() {
  local post_id=$1 attempt response status
  for attempt in $(seq 1 60); do
    response=$(auth_json "${TOKENS[0]}" "$API/api/v1/posts/$post_id")
    status=$(jq -r .status <<<"$response")
    [[ "$status" == published ]] && return 0
    sleep 1
  done
  printf 'Пост %s не перешёл в published за 60 секунд\n' "$post_id" >&2
  return 1
}
for id in "${NONCELEB_POSTS[@]}" "${CELEB_POSTS[@]}"; do wait_published "$id"; done
printf 'Опубликовано: %s обычных, %s celeb\n' "${#NONCELEB_POSTS[@]}" "${#CELEB_POSTS[@]}"

step "Проверить fanout PostgreSQL и Redis для последних 20 обычных постов"
auth_json "${TOKENS[0]}" "$API/api/v1/feed?limit=20" >/dev/null # materialize Redis feed cache
ordinary_ids=$(IFS=,; echo "${NONCELEB_POSTS[*]}")
pg_fanout=$(docker compose exec -T postgres psql -U app -d app -Atqc \
  "SELECT count(*) FROM feed_entries WHERE user_id=$RECIPIENT AND post_id IN ($ordinary_ids)")
assert_eq "$pg_fanout" 20 'обычные посты в PostgreSQL feed_entries'
pg_celeb=$(docker compose exec -T postgres psql -U app -d app -Atqc \
  "SELECT count(*) FROM feed_entries WHERE post_id IN ($(IFS=,; echo "${CELEB_POSTS[*]}"))")
assert_eq "$pg_celeb" 0 'celeb посты не fanout-ятся в PostgreSQL'
redis_feed=$(docker compose exec -T redis redis-cli ZCARD "feed:$RECIPIENT" | tr -d '\r')
assert_eq "$redis_feed" 20 'размер Redis ZSET fanout-кэша'
for id in "${NONCELEB_POSTS[@]}"; do
  score=$(docker compose exec -T redis redis-cli ZSCORE "feed:$RECIPIENT" "$id" | tr -d '\r')
  [[ -n "$score" && "$score" != nil ]] || { echo "Пост $id отсутствует в Redis feed cache" >&2; exit 1; }
done
redis_celeb=$(docker compose exec -T redis redis-cli ZCARD "user_posts:$CELEB" | tr -d '\r')
assert_eq "$redis_celeb" 100 'полный Redis ZSET постов celeb'

step "Проверить выдачу ленты, удаление поста и отписку"
feed=$(auth_json "${TOKENS[0]}" "$API/api/v1/feed?limit=20")
feed_count=$(jq '.items | length' <<<"$feed")
assert_eq "$feed_count" 20 'размер первой страницы'
feed_ids=$(jq -r '[.items[].id] | unique | length' <<<"$feed")
assert_eq "$feed_ids" 20 'уникальность постов в ленте'

updated_id=${CELEB_POSTS[0]}
updated=$(curl -sS --fail-with-body -H "Authorization: Bearer ${TOKENS[19]}" \
  -F 'text=celeb-updated' -X PUT "$API/api/v1/posts/$updated_id")
assert_eq "$(jq -r .text <<<"$updated")" 'celeb-updated' 'обновление метаданных поста'
for attempt in $(seq 1 30); do
  processed=$(docker compose exec -T postgres psql -U app -d app -Atqc \
    "SELECT count(*) FROM posts_outbox WHERE post_id=$updated_id AND event_type='UPDATED POST' AND processed_at IS NOT NULL")
  [[ "$processed" == 1 ]] && break
  sleep 0.2
done
assert_eq "$processed" 1 'обработка UPDATED POST outbox'

deleted_id=${NONCELEB_POSTS[19]}
auth_json "${TOKENS[18]}" -X DELETE "$API/api/v1/posts/$deleted_id" >/dev/null
after_delete=$(auth_json "${TOKENS[0]}" "$API/api/v1/feed?limit=20")
if jq -e --argjson id "$deleted_id" '.items[] | select(.id == $id)' <<<"$after_delete" >/dev/null; then
  echo "Удалённый пост $deleted_id остался в ленте" >&2; exit 1
fi
printf '✓ удалённый пост исключён из следующего ответа ленты\n'

auth_json "${TOKENS[0]}" -X DELETE "$API/api/v1/users/$CELEB/followers" >/dev/null
after_unfollow=$(auth_json "${TOKENS[0]}" "$API/api/v1/feed?limit=20")
if jq -e --argjson id "${CELEB_POSTS[99]}" '.items[] | select(.id == $id)' <<<"$after_unfollow" >/dev/null; then
  echo 'Пост celeb остался в ленте после отписки' >&2; exit 1
fi
printf '✓ посты celeb исключены после отписки\n'
step "Демо завершено"
