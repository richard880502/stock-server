"use client";

import { useEffect, useId, useRef, useState } from "react";
import { type SessionUser, signOut } from "@/lib/api/session";
import { ChevronDownIcon, LogoutIcon } from "./icons";

/** Profile picture, or the first letter of the name / email when it is missing or fails to load. */
function Avatar({ user, size }: { user: SessionUser; size: number }) {
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const initial = (user.name || user.email || "?").trim().charAt(0).toUpperCase();
  const showImage = user.picture && failedSrc !== user.picture;
  return (
    <span className="avatar" style={{ width: size, height: size }} aria-hidden="true">
      {showImage ? (
        // eslint-disable-next-line @next/next/no-img-element -- external Google URL, no optimizer
        <img
          src={user.picture ?? undefined}
          alt=""
          referrerPolicy="no-referrer"
          onError={() => setFailedSrc(user.picture)}
        />
      ) : (
        <span className="avatar-initial">{initial}</span>
      )}
    </span>
  );
}

/** Topbar account menu for Google sign-in: identity plus 登出. */
export function UserMenu({ user }: { user: SessionUser }) {
  const [open, setOpen] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const label = user.name || user.email;

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        trigger.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="user-menu" ref={root}>
      <button
        ref={trigger}
        type="button"
        className="user-trigger"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={`帳戶選單：${label}`}
        onClick={() => setOpen((value) => !value)}
      >
        <Avatar user={user} size={28} />
        <span className="user-trigger-name">{label}</span>
        <ChevronDownIcon size={16} className="user-trigger-chevron" />
      </button>
      {open && (
        <div className="user-panel" id={panelId} role="group" aria-label="帳戶">
          <div className="user-identity">
            <Avatar user={user} size={40} />
            <div className="user-lines">
              {user.name && <strong>{user.name}</strong>}
              <span>{user.email}</span>
            </div>
          </div>
          <button
            type="button"
            className="user-signout"
            disabled={leaving}
            onClick={() => {
              setLeaving(true);
              void signOut();
            }}
          >
            <LogoutIcon />
            {leaving ? "登出中…" : "登出"}
          </button>
        </div>
      )}
    </div>
  );
}
