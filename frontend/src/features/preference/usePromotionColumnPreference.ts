import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/authStore";
import {
  DEFAULT_COLUMN_PREFERENCE,
  getPromotionColumnPreference,
  savePromotionColumnPreference,
  type ColumnPreference,
} from "./columnPreference";

const keyFor = (userId: string | undefined) => ["column-preference", "promotion_list", userId];

export function usePromotionColumnPreference() {
  const userId = useAuthStore((state) => state.user?.id);
  const client = useQueryClient();
  const query = useQuery({
    queryKey: keyFor(userId),
    queryFn: getPromotionColumnPreference,
    enabled: !!userId,
    staleTime: Infinity,
    retry: false,
  });
  const mutation = useMutation({
    mutationFn: async ({ ownerId, preference }: { ownerId: string; preference: ColumnPreference }) => {
      if (useAuthStore.getState().user?.id !== ownerId) throw new Error("账号已切换，请重新打开列设置");
      await savePromotionColumnPreference(preference);
    },
    onSuccess: (_data, { ownerId, preference }) => client.setQueryData(keyFor(ownerId), preference),
  });
  return {
    userId,
    preference: query.data ?? DEFAULT_COLUMN_PREFERENCE,
    loading: query.isLoading,
    loadError: query.isError,
    retry: () => { void query.refetch(); },
    saving: mutation.isPending,
    save: (preference: ColumnPreference) => mutation.mutateAsync({ ownerId: userId!, preference }),
  };
}
