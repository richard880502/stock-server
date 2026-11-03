import type { ReactNode } from "react";

export function CardHeading({ title, aside }: { title: string; aside?: ReactNode }) {
  return (
    <div className="card-heading">
      <span>{title}</span>
      {aside}
    </div>
  );
}
