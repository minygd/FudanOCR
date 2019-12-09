#!/usr/bin/python
# -*- coding: utf-8 -*-

import os
import lmdb # install lmdb by "pip install lmdb"
import cv2
import numpy as np
import tools.dataset as dataset
import torch


def checkImageIsValid(imageBin):
    if imageBin is None:
        return False
    imageBuf = np.fromstring(imageBin, dtype=np.uint8)
    img = cv2.imdecode(imageBuf, cv2.IMREAD_GRAYSCALE)
    imgH, imgW = img.shape[0], img.shape[1]
    if imgH * imgW == 0:
        return False
    return True


def writeCache(env, cache):
    with env.begin(write=True) as txn:
        for k, v in cache.items():
            txn.put(str(k).encode(), str(v).encode())

# outputPath:输出路径
# imagePathList:图片路径List
# labelList:标签List
def createDataset(outputPath, imagePathList, labelList, lexiconList=None, checkValid=True):
    """
    Create LMDB dataset for CRNN training.
    ARGS:
        outputPath    : LMDB output path
        imagePathList : list of image path
        labelList     : list of corresponding groundtruth texts
        lexiconList   : (optional) list of lexicon lists
        checkValid    : if true, check the validity of every image
    """

    # imagePathList 对应和 labelList 配套
    assert(len(imagePathList) == len(labelList))

    # 获取数量
    nSamples = len(imagePathList)

    env = lmdb.open(outputPath, map_size=600000000)
    cache = {}
    cnt = 1
    for i in range(nSamples):
        imagePath = imagePathList[i]
        label = labelList[i]

        # 判断是否存在路径
        if not os.path.exists(imagePath):
            print('%s does not exist' % imagePath)
            continue
        # 直接读取图片
        with open(imagePath, 'rb') as f:
            imageBin = f.read()
        if checkValid:
            if not checkImageIsValid(imageBin):
                print('%s is not a valid image' % imagePath)
                continue

        imageKey = 'image-%09d' % cnt
        labelKey = 'label-%09d' % cnt
        cache[imageKey] = imageBin

        cache[labelKey] = label


        if lexiconList:
            lexiconKey = 'lexicon-%09d' % cnt
            cache[lexiconKey] = ' '.join(lexiconList[i])
        if cnt % 1000 == 0:
            writeCache(env, cache)
            cache = {}
            print('Written %d / %d' % (cnt, nSamples))
        cnt += 1
    nSamples = cnt-1
    cache['num-samples'] = str(nSamples)
    writeCache(env, cache)
    print('Created dataset with %d samples' % nSamples)


def read_image_label(image_directory,label_address):
    import os
    image_lis = os.listdir(image_directory)

    f = open(label_address,encoding='utf-8')
    dict = {}
    for line in f.readlines():
        dict[line[12:].split(" ")[0]] = line.split(' ')[1].replace('\n','').replace('\r','')
    # print(dict)

    result1 = []
    result2 = []

    for image_path1 in image_lis:
        for image_path2 in os.listdir(image_directory+'/'+image_path1):

            try:
            # image_path = image_path.replace('.jpg','')
                result1.append(image_directory+'/'+image_path1+'/'+image_path2)
                result2.append(dict[image_path1+'/'+image_path2])
            except:
                print("键值对未匹配")

    return result1,result2


def read_lmdb():
    # 读取IMDB格式的数据，并调整大小
    train_nips_dataset = dataset.lmdbDataset(root='./lmdb_test_output')
    return train_nips_dataset


if __name__ == '__main__':
    result1,result2 = read_image_label('G:/ART/ARTtrain','G:/ART/ARTtrain.txt')
    print(len(result1))
    print(len(result2))

    # createDataset('F:\\MORAN_v2\\lmdb_test_icdar',result1,result2)

    # train_dataset = read_lmdb()
    # train_loader = torch.utils.data.DataLoader(
    #     train_dataset, batch_size=64,
    #     shuffle=False,
    #     num_workers=2)
    #
    #
    # for i, (input,target) in enumerate(train_loader):
    #     pass




