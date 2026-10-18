# `[xu]_[Mac]hcxz` Unity3D LZ4 重打包记录

## 问题背景

输入模组为 `E:\[xu]_[Mac]hcxz.zipmod`。用户要求检查其中 Unity3D 的压缩方式；如果不是 LZ4，则按 LZ4 重压缩并重新打包，对比前后文件大小。

## 根因与判定

- zipmod 内只有一个 `abdata/chara/xu/[Mac]hcxz.unity3d`。
- 该文件是 UnityFS format 6、Unity `2018.2.21f1`，数据块 flags 为 `64`，低六位为 `0`，表示 Bundle 数据块未压缩（`none`）。
- zipmod 外层所有成员原本均为 ZIP Store；这与 UnityFS 内层 Bundle block compression 是两个独立层次。

## 解决方案

新增 `apps/scripts/repack_unity3d_lz4.py`。脚本只重写 UnityFS 外层 block layout：先解压原数据块，再按 128 KiB 分块使用 UnityPy 的 LZ4HC 压缩函数，写入 LZ4 flags，并保留 SerializedFile、资源流和目录节点的原始字节。脚本拒绝非 UnityFS format 6、加密 Bundle 和不支持的压缩类型，不覆盖输入文件。

输出文件：

- Unity3D：`apps/artifacts/xu_Mac_hcxz_lz4_test/outer_lz4.unity3d`
- zipmod：`apps/artifacts/[xu]_[Mac]hcxz_LZ4.zipmod`

## 验证结果

| 项目 | 原始 | LZ4 输出 | 变化 |
| --- | ---: | ---: | ---: |
| Unity3D | 63,334,395 B | 22,022,725 B | -41,311,670 B（-65.23%） |
| 完整 zipmod | 63,715,514 B | 22,404,276 B | -41,311,238 B（-64.84%） |

- 原 Unity3D SHA-256：`3c40c4eb0f759dbf71c22fd88ad235e417cacce905c625f0c97026e0ad8062cd`。
- 输出 Unity3D SHA-256：`8fd89bcbd44c1fcfcb06e173a5be30397d2bcf2c02c08773694fbb0beeb167fe`。
- 输出 zipmod SHA-256：`07bbc26f5e052ae8d648f6251e42a482f931afe080d59aa8bdb2f07bc48bef89`。
- 输出 Unity3D 检测为 `lz4`；UnityPy 可读取 1 个 AssetBundle、3892 个对象和 64 个容器项。
- 原始与输出解压后的 UnityFS payload SHA-256 均为 `4db14ada3cae3a513d3854fc7567c392ab05d16bb9f96a01a29fc69706204ece`，说明本次只改变外层压缩。
- 原始和输出 zip 均通过 `ZipFile.testzip()`；manifest、PNG、4 个 CSV 和目录项字节保持一致。

## 适用边界

该脚本适用于未加密的 UnityFS format 6 Bundle 外层压缩转换；它不解密自定义保护层，不重编码纹理，也不保证 UnityFS 以外的 UnityWeb/UnityRaw 或加密 Bundle 可处理。输出位于工作区隔离目录，原始 `E:` 文件未修改。
