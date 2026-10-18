# LS `PT0000480846` Unity3D 保护层诊断

## 样本与结论

2026-10-08 检查了用户提供的 `[LSZC]_fxcs_2.zipmod`：

```text
E:\game\HoneySelect 2 DX - TSYMQ\mods\StandardEditionAuthor\LS\[LSZC]_fxcs_2.zipmod
```

压缩包 SHA-256 为 `e569b5ffdcb6c023f3fbdd3163f0ca9cb8cbc3d8fef6783eebb7422b9e831079`，其中 `abdata/ls/fxcs.unity3d` 为 18,932,225 字节，SHA-256 为 `8d350450912a598437450dac3ab5aa067bd680d5ad37b67fa80e8aca712138ba`。它不以 `UnityFS` 或 XT `01 03 03 01` 开头，而是：

```text
PT0000480846 ...
```

去掉前 0x94 字节后，载荷的字节熵约为 8 bit/byte，UnityPy 只能建立一个输入流并得到 0 个对象；项目中的 `decrypt_unity3d.py` 和 `decrypt_xt_unity3d.py` 均按设计拒绝处理。

## 同作者对照

目标 manifest 的作者为 `LS`，GUID 为 `ls.cloth.fxcs`。同目录中可读的 LS 普通 UnityFS 参考为 `[LSZC]_blr.zipmod` 内的 `abdata/ls/blr.unity3d`：Unity `2018.2.21f1`，UnityPy 读取到 2,510 个对象。它只能证明作者同时发布过普通 UnityFS，不能提供目标文件的明文对应关系。

目标相关的同作者资源 `[lszc]_fxcs_1.zipmod` (`abdata/ls/fxcsAcc.unity3d`, SHA-256 `af4fc582abc592158fe3e41e4c9c2be71f3b7059882f1ab68d87ab7e4889899a9`) 和 `[LSZC]_tsxg.zipmod` (`abdata/ls/tsxg.unity3d`, SHA-256 `52470f79dc538061b6d18b25d38b364f9d63e596e373672d54a2343216d7eb17`) 也以相同 `PT0000480846` 标记开头，且载荷同样不可由 UnityPy 解析。这两个文件是同保护层的旁证，不是目标的明文原件。

在模组目录的其它位置还找到四个同作者、可读的普通 UnityFS 资源：`FXC01@LSworkqq1753890142`、`FXC02@LSworkqq1753890142`、`FXC04@LSworkqq1753890142` 和 `FXC08LSworkqq1753890142`。它们的 GUID、CSV 物品 ID、对象名和资源规模都与 `ls.cloth.fxcs` 不同，只能作为作者资源制作习惯的参考，不能替换目标对象。逐字节比较目标 PT 载荷与这些参考没有发现固定位置对应、重复 XOR 密钥或可直接套用的压缩块布局。

## 恢复边界

当前仓库记录的可恢复 profile 只覆盖标准 UnityFS、XT 循环 XOR、已验证的 Sakuraba 无原件 profile、已知偏移/流式纹理问题和已分析的固定包裹。`PT0000480846` 尚未有密钥、解包算法或目标逐资源明文原件证据。不能把普通 LS UnityFS 模板、`fxcsAcc`、`tsxg` 或任意同作者服装包复制到目标；这样会生成结构可读但资源内容错误的伪恢复文件。

额外尝试包括：对目标与同作者正常包做固定 XOR、单字节加减、仿射字节变换、常见压缩签名扫描，以及按 `UnityFS` 已知头推导循环密钥。目标载荷在头部之后保持约 8 bit/byte 熵，所有候选变换都无法得到自洽的 UnityFS 声明大小、block-info 或节点目录；因此没有可验证的离线恢复候选。

本次没有覆盖源文件，也没有生成 `.decrypted.unity3d`。若要继续，应取得同一 `ls.cloth.fxcs` 的未保护原件，或在关闭网络的隔离 HS2/BepInEx 环境中捕获该保护层的运行时解包结果；随后再按 UnityFS 头、对象表、纹理和 CSV `MainAB`/`MainData` 引用进行验证。
