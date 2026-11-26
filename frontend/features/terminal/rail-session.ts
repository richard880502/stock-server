export type SessionState = "pre" | "open" | "post" | "closed";

export const SESSION_LABEL: Record<SessionState, string> = {
  pre: "盤前",
  open: "盤中",
  post: "盤後",
  closed: "休市",
};

type TaipeiParts = { weekday: number; minutes: number; seconds: number; clock: string };

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const formatter = new Intl.DateTimeFormat("en-US", {
  timeZone: "Asia/Taipei",
  weekday: "short",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

/** Wall-clock parts in Asia/Taipei, independent of the viewer's timezone. */
export function taipeiParts(date: Date): TaipeiParts {
  const parts = Object.fromEntries(formatter.formatToParts(date).map((part) => [part.type, part.value]));
  const hour = Number(parts.hour) % 24;
  const minute = Number(parts.minute);
  const second = Number(parts.second);
  const pad = (n: number) => String(n).padStart(2, "0");
  return {
    weekday: WEEKDAYS.indexOf(parts.weekday),
    minutes: hour * 60 + minute,
    seconds: second,
    clock: `${pad(hour)}:${pad(minute)}:${pad(second)}`,
  };
}

/**
 * Taiwan cash-market session: 盤前 before 09:00, 盤中 09:00–13:30, 盤後 after 13:30.
 * Saturday and Sunday are 休市. There is NO holiday calendar: national holidays and
 * typhoon closures on weekdays will still show 盤前/盤中/盤後.
 */
export function marketSession(date: Date): SessionState {
  const { weekday, minutes } = taipeiParts(date);
  if (weekday === 0 || weekday === 6) return "closed";
  if (minutes < 9 * 60) return "pre";
  if (minutes < 13 * 60 + 30) return "open";
  return "post";
}

export const taipeiClock = (date: Date) => taipeiParts(date).clock;
