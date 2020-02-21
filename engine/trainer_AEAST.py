import time
from utils import utils
import torch
from torch.autograd import Variable
import os

from tqdm import tqdm
from torch.optim.lr_scheduler import LambdaLR

from engine.loss import getLoss
from engine.aeast_loss import LossFunc
from engine.optimizer import getOptimizer

from model.detection_model.AdvancedEAST.utils.utils import AverageMeter, save_log
from model.detection_model.AdvancedEAST.utils.earlystop import EarlyStopping


class LRPolicy:
    def __init__(self, rate, step):
        self.rate = rate
        self.step = step

    def __call__(self, it):
        return self.rate ** (it // self.step)


class Trainer(object):

    def __init__(self, modelObject, opt='', train_loader='', val_loader=''):
        '''
        模型训练器主体

        传入参数：
        1.未初始化的模型modelObject
        2.解析好的参数opt
        3.训练数据集的加载器train_loader
        4.验证数据集的加载器val_loader

        TODO：
        1.根据参数文件实例化模型
        2.配置Loss与Optimizer
        '''

        # 基本信息
        self.opt = opt

        '''初始化模型，并且转载预训练模型'''
        self.model = self.initModel(modelObject)
        self.loadParam()

        self.optimizer = getOptimizer(self.model, self.opt)
        self.criterion = LossFunc()
        # self.criterion = getLoss(self.opt)

        self.train_loader = train_loader
        self.val_loader = val_loader

        self.tick = time.strftime("%Y%m%d-%H-%M-%S", time.localtime(time.time()))
        self.earlystopping = EarlyStopping(opt.MODEL.PATIENCE, None)
        self.scheduler = LambdaLR(self.optimizer, lr_lambda=LRPolicy(rate=opt.MODEL.DECAY_RATE, step=opt.MODEL.DECAY_STEP))

        '''识别模型工具元件'''
        self.loss_avg = utils.averager()
        # self.converter = utils.strLabelConverterForAttention(self.alphabet.str)

        '''常量区'''
        self.i = 0
        self.highestAcc = 0

    def initModel(self, modelObject):
        '''
        根据配置文件初始化模型
        '''
        if self.opt.BASE.CUDA:
            return modelObject().cuda()
            # return modelObject(self.opt, self.alphabet).cuda()
        else:
            modelObject()

    def loadParam(self):
        '''
        预加载训练参数
        '''
        if os.path.isfile(self.opt.ADDRESS.PRETRAIN_MODEL_DIR):

            address = self.opt.ADDRESS.PRETRAIN_MODEL_DIR
            print('loading pretrained model from %s' % address)
            if opt.CUDA:
                state_dict = torch.load(address)
            else:
                state_dict = torch.load(address, map_location='cpu')
            state_dict_rename = OrderedDict()
            for k, v in state_dict.items():
                name = k.replace("module.", "")  # remove `module.`
                state_dict_rename[name] = v
            self.model.load_state_dict(state_dict_rename, strict=True)

    def pretreatment(self, data):
        '''
        将从dataloader加载出来的data转化为可以传入神经网络的数据
        '''
        image = torch.FloatTensor(self.opt.MODEL.BATCH_SIZE, self.opt.IMAGE.IMG_CHANNEL, self.opt.IMAGE.IMG_H,
                                  self.opt.IMAGE.IMG_H)
        text = torch.LongTensor(self.opt.MODEL.BATCH_SIZE * 5)
        text_rev = torch.LongTensor(self.opt.MODEL.BATCH_SIZE * 5)
        length = torch.IntTensor(self.opt.MODEL.BATCH_SIZE)

        if self.opt.CUDA:
            # self.model = torch.nn.DataParallel(self.model, device_ids=range(self.opt.ngpu))
            image = image.cuda()
            text = text.cuda()
            text_rev = text_rev.cuda()
            self.criterion = self.criterion.cuda()

        image = Variable(image)
        text = Variable(text)
        text_rev = Variable(text_rev)
        length = Variable(length)

        if self.opt.BidirDecoder:
            cpu_images, cpu_texts, cpu_texts_rev = data
            utils.loadData(image, cpu_images)
            t, l = self.converter.encode(cpu_texts, scanned=True)
            t_rev, _ = self.converter.encode(cpu_texts_rev, scanned=True)
            utils.loadData(text, t)
            utils.loadData(text_rev, t_rev)
            utils.loadData(length, l)
            return image, length, text, text_rev
            # preds0, preds1 = self.model(image, length, text, text_rev)
            # cost = self.criterion(torch.cat([preds0, preds1], 0), torch.cat([text, text_rev], 0))
        else:
            cpu_images, cpu_texts = data
            utils.loadData(image, cpu_images)
            t, l = self.converter.encode(cpu_texts, scanned=True)
            utils.loadData(text, t)
            utils.loadData(length, l)
            return image, length, text, text_rev
            # preds = self.model(image, length, text, text_rev)
            # cost = self.criterion(preds, text)

    def posttreatment(self, modelResult, pretreatmentData, originData, test=False):
        '''
        将神经网络传出的数据解码为可用于计算结果的数据
        '''

        if test == False:
            if self.opt.BidirDecoder:
                image, length, text, text_rev = pretreatmentData
                preds0, preds1 = modelResult
                cost = self.criterion(torch.cat([preds0, preds1], 0), torch.cat([text, text_rev], 0))
            else:
                image, length, text, text_rev = pretreatmentData
                preds = self.model(image, length, text, text_rev)
                cost = self.criterion(preds, text)

            return cost

        else:
            if self.opt.BidirDecoder:
                preds0, preds1 = modelResult
                cpu_images, cpu_texts, cpu_texts_rev = originData
                image, length, text, text_rev = pretreatmentData

                cost = self.criterion(torch.cat([preds0, preds1], 0), torch.cat([text, text_rev], 0))
                preds0, preds1 = modelResult
                preds0_prob, preds0 = preds0.max(1)
                preds0 = preds0.view(-1)
                preds0_prob = preds0_prob.view(-1)
                sim_preds0 = self.converter.decode(preds0.data, length.data)
                preds1_prob, preds1 = preds1.max(1)
                preds1 = preds1.view(-1)
                preds1_prob = preds1_prob.view(-1)
                sim_preds1 = self.converter.decode(preds1.data, length.data)
                sim_preds = []
                for j in range(cpu_images.size(0)):
                    text_begin = 0 if j == 0 else length.data[:j].sum()
                    if torch.mean(preds0_prob[text_begin:text_begin + len(sim_preds0[j].split('$')[0] + '$')]).data[0] > \
                            torch.mean(
                                preds1_prob[text_begin:text_begin + len(sim_preds1[j].split('$')[0] + '$')]).data[0]:
                        sim_preds.append(sim_preds0[j].split('$')[0] + '$')
                    else:
                        sim_preds.append(sim_preds1[j].split('$')[0][-1::-1] + '$')

                return cost, sim_preds, cpu_texts
            else:
                cpu_images, cpu_texts = originData
                preds = modelResult
                image, length, text, text_rev = pretreatmentData
                cost = self.criterion(preds, text)
                _, preds = preds.max(1)
                preds = preds.view(-1)
                sim_preds = self.converter.decode(preds.data, length.data)

                return cost, sim_preds, cpu_texts

    def validate(self):
        '''
        在特定训练次数后执行验证模型能力操作
        '''

        acc_tmp = 0
        self.setModelState('test')

        print('Start val')
        val_loader = self.val_loader
        val_iter = iter(val_loader)
        n_correct = 0
        n_total = 0
        distance = 0.0
        loss_avg = self.loss_avg

        f = open('./OCR新架构验证测试.txt', 'a', encoding='utf-8')

        for i in range(len(val_loader)):
            data = val_iter.next()

            pretreatmentData = self.pretreatment(data)

            modelResult = self.model(*pretreatmentData)

            cost, preds, targets = self.posttreatment(modelResult, pretreatmentData, originData=data, test=True)

            loss_avg.add(cost)

            for pred, target in zip(preds, targets):
                if pred == target.lower():
                    n_correct += 1
                f.write("预测 %s      目标 %s\n" % (pred, target))
                distance += Levenshtein.distance(pred, target) / max(len(pred), len(target))
                n_total += 1

        f.close()
        accuracy = n_correct / float(n_total)

        print("correct / total: %d / %d, " % (n_correct, n_total))
        print('levenshtein distance: %f' % (distance / n_total))
        print('Test loss: %f, accuray: %f' % (loss_avg.val(), accuracy))

        if acc_tmp > self.highestAcc:
            self.highestAcc = acc_tmp
            torch.save(self.model.state_dict(), '{0}/{1}_{2}.pth'.format(
                self.opt.ADDRESS.CHECKPOINTS_DIR, self.i, str(self.highestAcc)[:6]))
        return acc_tmp

    def train(self):
        '''
        训练函数
        Already Done:
        1.初始化一个可训练模型
        2.定义损失函数与优化器

        TODO:
        1.定义一个epoch循环，循环总数在配置文件中定义。每个epoch会把所有
        2.定义一个train_loader迭代器，在每一个iter里，使用next()获取下一个批次
            对于每一个iter,计算模数，在对应的迭代周期里执行保存/验证/记录 操作
        '''
        losses = AverageMeter()
        # losses = AverageMeter()
        epoch = self.opt.MODEL.EPOCH

        self.model.train()
        for i, (img, gt) in tqdm(enumerate(self.train_loader), desc='Train', total=len(self.train_loader)):
            img = img.cuda()
            gt = gt.cuda()
            east_detect = self.model(img)
            # print(gt.requires_grad, east_detect.requires_grad)  # false true
            # east_detect = east_detect.detach()
            # loss = self.criterion(gt, east_detect.long())
            loss = self.criterion(gt, east_detect)
            losses.update(loss.item(), img.size(0))

            # backward propagation
            self.scheduler.step()
            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()

            if (i + 1) % self.opt.SHOW_FREQ == 0:
                tqdm.write(
                    'Training loss - Epoch: [{0}][{1}/{2}] Loss {loss.val:.4f} Avg Loss {loss.avg:.4f}'.format(
                        epoch, i + 1, len(self.train_loader), loss=losses))
            save_log(losses, epoch, i + 1, len(self.train_loader), self.tick, split='Training')

    def checkSaveOrVal(self):
        '''验证'''
        if self.i % self.opt.VAL_FREQ == 0:
            self.setModelState('test')
            acc_tmp = self.validate()
            '''记录训练结果最大值的模型文件'''
            if acc_tmp > self.highestAcc:
                self.highestAcc = acc_tmp
                torch.save(self.model.state_dict(), '{0}/{1}_{2}.pth'.format(
                    self.opt.ADDRESS.CHECKPOINTS_DIR, self.i, str(self.highestAcc)[:6]))

        '''保存'''
        if self.i % self.opt.SAVE_FREQ == 0:
            torch.save(self.model.state_dict(), '{0}/{1}_{2}.pth'.format(
                self.opt.ADDRESS.CHECKPOINTS_DIR, self.opt.MODEL.EPOCH, self.i))

        '''恢复训练状态'''
        self.setModelState('train')

    def setModelState(self,state):
        '''
        根据传入的状态判断模型处于训练或者验证状态
        '''
        if state == 'eval' or state == 'test':
            for p in self.model.parameters():
                p.requires_grad = False
            self.model.eval()
        elif state == 'train':
            for p in self.model.parameters():
                p.requires_grad = True
            self.model.train()


