import { Routes, Route, Navigate } from "react-router-dom";
import Layout from "@/components/layout/Layout";
import LoginPage from "@/pages/LoginPage";
import DashboardPage from "@/pages/DashboardPage";
import ActiveDeliveriesPage from "@/pages/ActiveDeliveriesPage";
import DeliveryHistoryPage from "@/pages/DeliveryHistoryPage";
import DeliveryDetailPage from "@/pages/DeliveryDetailPage";
import DispatchersPage from "@/pages/DispatchersPage";
import WalletPage from "@/pages/WalletPage";
import BlacklistPage from "@/pages/BlacklistPage";
import ReportsPage from "@/pages/ReportsPage";
import GroupSettingsPage from "@/pages/GroupSettingsPage";
import { Toaster } from "@/components/ui/toaster";

export default function App() {
  return (
    <>
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
      <Toaster />
    </>
  );
}
