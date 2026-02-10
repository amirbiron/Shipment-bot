import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { type ColumnDef } from "@tanstack/react-table";
import { getActiveDeliveries, type DeliveryItem } from "@/api/deliveries";
import DataTable from "@/components/shared/DataTable";
import Pagination from "@/components/shared/Pagination";
import StatusBadge from "@/components/shared/StatusBadge";
import { formatDate, formatCurrency } from "@/lib/format";

const columns: ColumnDef<DeliveryItem, unknown>[] = [
  { accessorKey: "id", header: "#", cell: ({ row }) => row.original.id },
  { accessorKey: "pickup_address", header: "מ", cell: ({ row }) => (
    <span className="max-w-[150px] truncate block">{row.original.pickup_address}</span>
  )},
  { accessorKey: "dropoff_address", header: "אל", cell: ({ row }) => (
    <span className="max-w-[150px] truncate block">{row.original.dropoff_address}</span>
  )},
  { accessorKey: "status", header: "סטטוס", cell: ({ row }) => (
    <StatusBadge status={row.original.status} />
  )},
  { accessorKey: "fee", header: "עמלה", cell: ({ row }) => formatCurrency(row.original.fee) },
  { accessorKey: "courier_name", header: "שליח", cell: ({ row }) => row.original.courier_name || "-" },
  { accessorKey: "sender_name", header: "שולח", cell: ({ row }) => row.original.sender_name || "-" },
  { accessorKey: "created_at", header: "תאריך", cell: ({ row }) => formatDate(row.original.created_at) },
];

export default function ActiveDeliveriesPage() {
  const [page, setPage] = useState(1);
  const navigate = useNavigate();

  const { data, isLoading } = useQuery({
    queryKey: ["deliveries", "active", page],
    queryFn: () => getActiveDeliveries(page),
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
  });

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-bold">משלוחים פעילים</h2>
      <DataTable
        columns={columns}
        data={data?.items ?? []}
        isLoading={isLoading}
        emptyMessage="אין משלוחים פעילים"
        onRowClick={(row) => navigate(`/deliveries/${row.id}`)}
      />
      {data && (
        <Pagination
          page={data.page}
          totalPages={data.total_pages}
          onPageChange={setPage}
        />
      )}
    </div>
  );
}
