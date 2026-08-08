import { apiClient } from "@/services/apiClient";
import type {
  Credential,
  CredentialCreatePayload,
  CredentialListParams,
  CredentialPage,
  CredentialUpdatePayload,
} from "./types";

const BASE_PATH = "/api/credentials";

export async function listCredentials(
  params: CredentialListParams = {},
): Promise<CredentialPage> {
  const response = await apiClient.get<CredentialPage>(`${BASE_PATH}/`, { params });
  return response.data;
}

export async function createCredential(
  payload: CredentialCreatePayload,
): Promise<Credential> {
  const response = await apiClient.post<Credential>(`${BASE_PATH}/`, payload);
  return response.data;
}

export async function updateCredential(
  id: string,
  payload: CredentialUpdatePayload,
): Promise<Credential> {
  const response = await apiClient.put<Credential>(`${BASE_PATH}/${id}`, payload);
  return response.data;
}

export async function pauseCredential(id: string): Promise<Credential> {
  const response = await apiClient.put<Credential>(`${BASE_PATH}/${id}/pause`);
  return response.data;
}

export async function resumeCredential(id: string): Promise<Credential> {
  const response = await apiClient.put<Credential>(`${BASE_PATH}/${id}/resume`);
  return response.data;
}

export async function deleteCredential(id: string): Promise<void> {
  await apiClient.delete(`${BASE_PATH}/${id}`);
}
