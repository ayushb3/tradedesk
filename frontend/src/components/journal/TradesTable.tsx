import { useMemo, useState } from "react";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingState,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { Trade } from "@/lib/api";
import {
  formatDayMonth,
  formatNumber,
  formatPnl,
  pnlColorClass,
} from "@/lib/format";
import { cn } from "@/lib/utils";

interface TradesTableProps {
  trades: Trade[];
  onSelect: (id: number) => void;
}

const col = createColumnHelper<Trade>();

/** Sortable trade grid. Row click navigates to the trade cockpit. */
export function TradesTable({ trades, onSelect }: TradesTableProps) {
  const [sorting, setSorting] = useState<SortingState>([
    { id: "session_date", desc: true },
  ]);

  const columns = useMemo(
    () => [
      col.accessor("session_date", {
        header: "Date",
        cell: (c) => (
          <span className="tabular whitespace-nowrap text-muted-foreground">
            {formatDayMonth(c.getValue())}
          </span>
        ),
      }),
      col.accessor("symbol", {
        header: "Symbol",
        cell: (c) => (
          <span className="font-medium">
            {c.row.original.contract_label ?? c.getValue()}
          </span>
        ),
      }),
      col.accessor("direction", {
        header: "Dir",
        cell: (c) => (
          <span
            className={
              c.getValue() === "long"
                ? "text-[#26a69a]"
                : "text-[#ef5350]"
            }
          >
            {c.getValue() === "long" ? "Long" : "Short"}
          </span>
        ),
      }),
      col.accessor("qty", {
        header: "Qty",
        cell: (c) => (
          <span className="tabular">{formatNumber(c.getValue(), 0)}</span>
        ),
      }),
      col.accessor("avg_entry", {
        header: "Entry",
        cell: (c) => (
          <span className="tabular">{formatNumber(c.getValue())}</span>
        ),
      }),
      col.accessor("avg_exit", {
        header: "Exit",
        cell: (c) => (
          <span className="tabular">{formatNumber(c.getValue())}</span>
        ),
      }),
      col.accessor("net_pnl", {
        header: "Net P&L",
        cell: (c) => (
          <span className={"tabular font-medium " + pnlColorClass(c.getValue())}>
            {formatPnl(c.getValue())}
          </span>
        ),
      }),
      col.accessor("r_multiple", {
        header: "R",
        cell: (c) => (
          <span className={"tabular " + pnlColorClass(c.getValue())}>
            {formatNumber(c.getValue())}
          </span>
        ),
      }),
      col.accessor((t) => t.enrichment?.grade ?? null, {
        id: "grade",
        header: "Grade",
        cell: (c) => {
          const g = c.getValue();
          return g ? (
            <Badge variant="outline" className="tabular">
              {g}
            </Badge>
          ) : (
            <span className="text-muted-foreground">—</span>
          );
        },
      }),
      col.accessor((t) => t.tags?.map((tag) => tag.name).join(", ") ?? "", {
        id: "tags",
        header: "Tags",
        enableSorting: false,
        cell: (c) => {
          const tags = c.row.original.tags ?? [];
          if (tags.length === 0)
            return <span className="text-muted-foreground">—</span>;
          return (
            <div className="flex max-w-[220px] flex-wrap gap-1">
              {tags.slice(0, 3).map((t) => (
                <Badge key={t.id} variant="muted">
                  {t.name}
                </Badge>
              ))}
              {tags.length > 3 && (
                <Badge variant="muted">+{tags.length - 3}</Badge>
              )}
            </div>
          );
        },
      }),
    ],
    [],
  );

  const table = useReactTable({
    data: trades,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((hg) => (
            <TableRow key={hg.id} className="hover:bg-transparent">
              {hg.headers.map((header) => {
                const sortable = header.column.getCanSort();
                const sort = header.column.getIsSorted();
                return (
                  <TableHead key={header.id}>
                    {sortable ? (
                      <button
                        type="button"
                        onClick={header.column.getToggleSortingHandler()}
                        className="inline-flex items-center gap-1 hover:text-foreground"
                      >
                        {flexRender(
                          header.column.columnDef.header,
                          header.getContext(),
                        )}
                        {sort === "asc" ? (
                          <ArrowUp className="h-3 w-3" />
                        ) : sort === "desc" ? (
                          <ArrowDown className="h-3 w-3" />
                        ) : (
                          <ChevronsUpDown className="h-3 w-3 opacity-40" />
                        )}
                      </button>
                    ) : (
                      flexRender(
                        header.column.columnDef.header,
                        header.getContext(),
                      )
                    )}
                  </TableHead>
                );
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.map((row) => (
            <TableRow
              key={row.id}
              onClick={() => onSelect(row.original.id)}
              className={cn("cursor-pointer text-sm")}
            >
              {row.getVisibleCells().map((cell) => (
                <TableCell key={cell.id}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
