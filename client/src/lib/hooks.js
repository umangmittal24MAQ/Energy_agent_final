import { useQuery } from "@tanstack/react-query";
import { fetchKpis, fetchUnifiedData, fetchInverterUptime, fetchWeather } from "./api";


export function useWeather() {
  return useQuery({
    queryKey: ["weather"],
    queryFn: fetchWeather,
    staleTime: 9 * 60 * 1000,    // server caches 10 min, re-fetch just before
    refetchInterval: 10 * 60 * 1000,  // auto-refresh every 10 min
    refetchOnWindowFocus: false,  // avoid hammering on tab switch
    retry: 1,
  });
}


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

export function useTemperatureRecommendation() {
  return useQuery({
    queryKey: ["temperature-recommendation"],
    queryFn: fetchTemperatureRecommendation,
    staleTime: 9 * 60 * 1000,
    refetchInterval: 10 * 60 * 1000,
    refetchOnWindowFocus: false,
    retry: 1,
  });

}