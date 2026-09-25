"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  createPublicClient,
  createWalletClient,
  custom,
  http,
  type Address,
  type PublicClient,
  type WalletClient,
} from "viem";
import { covenantAbi, type CovenantConfig } from "@covenant/sdk";
import { configuredChain, contractAddress, rpcUrl, txErrorMessage } from "../lib/chain";

/**
 * Wallet + contract context for the challenge game. One provider owns the
 * injected wallet connection (window.ethereum via viem's custom transport),
 * the chain check against NEXT_PUBLIC_CHAIN, and the contract's config()
 * read; every button below the fold consumes the same state so the desk and
 * the epoch rows never disagree.
 */

interface ChallengeContextValue {
  /** Demo mode: NEXT_PUBLIC_CONTRACT_ADDRESS is unset or malformed. */
  demo: boolean;
  contract: Address | null;
  chainId: number | null;
  chainLabel: string;
  publicClient: PublicClient | null;
  wallet: WalletClient | null;
  address: Address | null;
  walletChainId: number | null;
  chainOk: boolean;
  /** Null until the first config() read settles; configError explains failures. */
  config: CovenantConfig | null;
  configError: string | null;
  refreshConfig: () => void;
  connect: () => Promise<void>;
  disconnect: () => void;
  switchToCovenant: () => Promise<void>;
  walletError: string | null;
}

const ChallengeContext = createContext<ChallengeContextValue | null>(null);

export function useChallenge(): ChallengeContextValue {
  const ctx = useContext(ChallengeContext);
  if (ctx === null) throw new Error("useChallenge must be used inside ChallengeProvider");
  return ctx;
}

export default function ChallengeProvider({ children }: { children: ReactNode }) {
  const contract = useMemo(() => contractAddress(), []);
  const chain = useMemo(() => configuredChain(), []);
  const rpc = useMemo(() => rpcUrl(), []);

  const publicClient = useMemo(
    () => (chain !== null && rpc !== "" ? createPublicClient({ chain, transport: http(rpc) }) : null),
    [chain, rpc],
  );

  const [wallet, setWallet] = useState<WalletClient | null>(null);
  const [address, setAddress] = useState<Address | null>(null);
  const [walletChainId, setWalletChainId] = useState<number | null>(null);
  const [walletError, setWalletError] = useState<string | null>(null);
  const [config, setConfig] = useState<CovenantConfig | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [configTick, setConfigTick] = useState(0);

  const refreshConfig = useCallback(() => setConfigTick((t) => t + 1), []);

  // config() read: independent of the wallet, retried on refreshConfig().
  useEffect(() => {
    if (publicClient === null || contract === null) return;
    let dead = false;
    (async () => {
      try {
        const raw = await publicClient.readContract({
          address: contract,
          abi: covenantAbi,
          functionName: "config",
        });
        const [admin, resolver, usdg, bondAmount, challengeStake, challengeWindow, paused, treasury] =
          raw as readonly [Address, Address, Address, bigint, bigint, bigint, boolean, bigint];
        if (dead) return;
        setConfig({ admin, resolver, usdg, bondAmount, challengeStake, challengeWindow, paused, treasury });
        setConfigError(null);
      } catch (e) {
        if (!dead) setConfigError(txErrorMessage(e));
      }
    })();
    return () => {
      dead = true;
    };
  }, [publicClient, contract, configTick]);

  // Silent session restore: pick up an already-authorized account, never prompt.
  useEffect(() => {
    const eth = window.ethereum;
    if (eth === undefined || chain === null) return;
    let dead = false;
    (async () => {
      try {
        const accounts = (await eth.request({ method: "eth_accounts" })) as string[];
        if (dead || accounts.length === 0) return;
        const w = createWalletClient({
          account: accounts[0] as Address,
          chain,
          transport: custom(eth),
        });
        setWallet(w);
        setAddress(accounts[0] as Address);
        setWalletChainId(await w.getChainId());
      } catch {
        // stay disconnected; the connect button remains the path forward
      }
    })();
    return () => {
      dead = true;
    };
  }, [chain]);

  // Follow the wallet's account and network switches while connected.
  useEffect(() => {
    const eth = window.ethereum;
    if (eth === undefined || eth.on === undefined) return;
    const onAccounts = (accs: unknown) => {
      const list = Array.isArray(accs) ? (accs as string[]) : [];
      setAddress(list.length > 0 ? (list[0] as Address) : null);
      if (list.length === 0) setWallet(null);
    };
    const onChain = (cid: unknown) => {
      const n = typeof cid === "string" ? Number(cid) : Number.NaN;
      setWalletChainId(Number.isFinite(n) ? n : null);
    };
    eth.on("accountsChanged", onAccounts);
    eth.on("chainChanged", onChain);
    return () => {
      eth.removeListener?.("accountsChanged", onAccounts);
      eth.removeListener?.("chainChanged", onChain);
    };
  }, []);

  const connect = useCallback(async () => {
    const eth = window.ethereum;
    if (eth === undefined || chain === null) {
      setWalletError("no injected wallet found in this browser");
      return;
    }
    try {
      const w = createWalletClient({ chain, transport: custom(eth) });
      const accounts = await w.requestAddresses();
      if (accounts.length === 0) throw new Error("wallet returned no accounts");
      setWallet(w);
      setAddress(accounts[0] ?? null);
      setWalletChainId(await w.getChainId());
      setWalletError(null);
    } catch (e) {
      setWalletError(txErrorMessage(e));
    }
  }, [chain]);

  const disconnect = useCallback(() => {
    setWallet(null);
    setAddress(null);
    setWalletChainId(null);
    setWalletError(null);
  }, []);

  const switchToCovenant = useCallback(async () => {
    const eth = window.ethereum;
    if (eth === undefined || chain === null) return;
    try {
      await eth.request({
        method: "wallet_switchEthereumChain",
        params: [{ chainId: `0x${chain.id.toString(16)}` }],
      });
      setWalletChainId(chain.id);
      setWalletError(null);
    } catch (e) {
      const code = (e as { code?: number }).code;
      setWalletError(
        code === 4902
          ? `wallet does not know ${chain.name}; add chain id ${chain.id} to it manually`
          : txErrorMessage(e),
      );
    }
  }, [chain]);

  const chainOk = walletChainId !== null && chain !== null && walletChainId === chain.id;

  const value = useMemo<ChallengeContextValue>(
    () => ({
      demo: contract === null,
      contract,
      chainId: chain?.id ?? null,
      chainLabel: chain?.name ?? "unknown chain",
      publicClient,
      wallet,
      address,
      walletChainId,
      chainOk,
      config,
      configError,
      refreshConfig,
      connect,
      disconnect,
      switchToCovenant,
      walletError,
    }),
    [
      contract,
      chain,
      publicClient,
      wallet,
      address,
      walletChainId,
      chainOk,
      config,
      configError,
      refreshConfig,
      connect,
      disconnect,
      switchToCovenant,
      walletError,
    ],
  );

  return <ChallengeContext.Provider value={value}>{children}</ChallengeContext.Provider>;
}
