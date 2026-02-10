import { lazy, Suspense } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import Layout from "@/components/layout/Layout";
import ErrorBoundary from "@/components/shared/ErrorBoundary";
import { Toaster } from "@/components/ui/toaster";
import { Skeleton } from "@/components/ui/skeleton";

// טעינה עצלנית של דפים — מקטינה את ה-bundle הראשוני
const LoginPage = lazy(() => import("@/pages/LoginPage"));
const DashboardPage = lazy(() => import("@/pages/DashboardPage"));
const ActiveDeliveriesPage = lazy(() => import("@/pages/ActiveDeliveriesPage"));
const DeliveryHistoryPage = lazy(() => import("@/pages/DeliveryHistoryPage"));
const DeliveryDetailPage = lazy(() => import("@/pages/DeliveryDetailPage"));
const DispatchersPage = lazy(() => import("@/pages/DispatchersPage"));
const WalletPage = lazy(() => import("@/pages/WalletPage"));
const BlacklistPage = lazy(() => import("@/pages/BlacklistPage"));
const ReportsPage = lazy(() => import("@/pages/ReportsPage"));
const GroupSettingsPage = lazy(() => import("@/pages/GroupSettingsPage"));

function PageLoader() {
  return (
    <div className="space-y-4 p-4">
      <Skeleton className="h-10 w-48" />
      <Skeleton className="h-64" />
    </div>
  );
}

export default function App() {
  return (
    <ErrorBoundary>
      <Suspense fallback={<PageLoader />}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<Layout />}>
            <Route index element={<DashboardPage />} />
            <Route path="deliveries/active" element={<ActiveDeliveriesPage />} />
            <Route
              path="deliveries/history"
              element={<DeliveryHistoryPage />}
            />
            <Route path="deliveries/:id" element={<DeliveryDetailPage />} />
            <Route path="dispatchers" element={<DispatchersPage />} />
            <Route path="wallet" element={<WalletPage />} />
            <Route path="blacklist" element={<BlacklistPage />} />
            <Route path="reports" element={<ReportsPage />} />
            <Route path="groups" element={<GroupSettingsPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
      <Toaster />
    </ErrorBoundary>
  );
}
