import { useQuery } from "@tanstack/react-query";
import { fetchKpis, fetchUnifiedData, fetchInverterUptime} from "./api";


export function useInverterUptime() {
  return useQuery({
    queryKey: ["inverter-uptime"],
    queryFn: fetchInverterUptime,
    staleTime: 0,          // always re-fetch on tab visit
    refetchOnMount: true,  // fires every time Solar tab mounts
  });
}

export function useKpis(params = {}) {
  return useQuery({
    queryKey: ["kpis", params.startDate ?? null, params.endDate ?? null],
    queryFn: () => fetchKpis(params),
  });
}

export function useUnifiedData() {
  return useQuery({
    queryKey: ["unified-data"],
    queryFn: fetchUnifiedData,
    staleTime: 5 * 60 * 1000,   // re-fetch after 5 mins
    refetchOnMount: true,        // always re-fetch when tab mounts
    refetchOnWindowFocus: true,  // re-fetch when user switches back to window
  });
}
