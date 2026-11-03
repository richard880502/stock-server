import type { ReactNode } from "react";

export function Card({
  title,
  sub,
  aside,
  children,
  className = "",
  as: Tag = "section",
  ...rest
}: {
  title?: string;
  sub?: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
  as?: "section" | "article" | "div";
  "aria-label"?: string;
}) {
  return (
    <Tag className={`card ${className}`.trim()} {...rest}>
      {(title || aside) && (
        <div className="card-header">
          <div>
            {title && <h2 className="card-title">{title}</h2>}
            {sub && <p className="card-sub">{sub}</p>}
          </div>
          {aside}
        </div>
      )}
      {children}
    </Tag>
  );
}
