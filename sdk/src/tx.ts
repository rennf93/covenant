import type { Abi, Address, Hash, PublicClient, TransactionReceipt, WalletClient } from "viem";

/**
 * Confirmation plumbing shared by every mutating client: no caller ever sees a
 * fire-and-forget hash; a returned result always carries the mined receipt.
 */

/** Result of a confirmed write: the tx hash plus its mined receipt. */
export interface TxResult {
  hash: Hash;
  receipt: TransactionReceipt;
}

/** Waits for `hash` to mine and returns its receipt. */
export async function confirm(client: PublicClient, hash: Hash): Promise<TransactionReceipt> {
  return client.waitForTransactionReceipt({ hash });
}

/** Sends via `write` and waits for the receipt before returning. */
export async function sendAndWait(
  client: PublicClient,
  write: () => Promise<Hash>,
): Promise<TxResult> {
  const hash = await write();
  if (!hash) throw new Error("wallet returned no transaction hash");
  const receipt = await confirm(client, hash);
  return { hash, receipt };
}

/**
 * Sends a contract write whose abi/function are chosen at runtime (admin,
 * operator) and waits for the receipt. The request is cast once here because
 * viem's writeContract typing expects literal abi entries; the runtime contract
 * is the same.
 */
export async function writeAndWait(args: {
  client: PublicClient;
  wallet: WalletClient;
  address: Address;
  abi: Abi;
  functionName: string;
  args?: readonly unknown[] | undefined;
  account: Address;
}): Promise<TxResult> {
  const request = {
    address: args.address,
    abi: args.abi,
    functionName: args.functionName,
    args: args.args,
    account: args.account,
    chain: args.wallet.chain,
  } as unknown as Parameters<WalletClient["writeContract"]>[0];
  return sendAndWait(args.client, () => args.wallet.writeContract(request));
}
