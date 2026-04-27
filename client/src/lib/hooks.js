import { useQuery } from "@tanstack/react-query";
import { fetchKpis, fetchUnifiedData } from "./api";

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
  });
}
