import type { ReactNode } from "react";

export function ResearchTable({
  label,
  headers,
  children,
}: {
  /** Accessible name of the table. */
  label: string;
  headers: string[];
  children: ReactNode;
}) {
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label={label}>
      <table className="data-table">
        <thead>
          <tr>
            {headers.map((header) => (
              <th key={header} scope="col">
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
