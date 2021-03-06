import numpy as np
import torch
from torch import nn
from torch.utils.tensorboard import SummaryWriter
from tqdm import tqdm

from config import device, im_size, grad_clip, print_freq, num_workers
from data_gen import DIMDataset
from models.deeplab import DeepLab
from test import test
from utils import parse_args, save_checkpoint, AverageMeter, clip_gradient, get_logger, get_learning_rate, \
    alpha_prediction_loss


def train_net(args):
    torch.manual_seed(7)
    np.random.seed(7)
    checkpoint = args.checkpoint
    start_epoch = 0
    best_loss = float('inf')
    writer = SummaryWriter()
    epochs_since_improvement = 0

    # Initialize / load checkpoint
    if checkpoint is None:
        model = DeepLab(backbone='mobilenet', output_stride=16, num_classes=1)
        model = nn.DataParallel(model)

        optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)

    else:
        checkpoint = torch.load(checkpoint)
        start_epoch = checkpoint['epoch'] + 1
        epochs_since_improvement = checkpoint['epochs_since_improvement']
        model = checkpoint['model'].module
        model = nn.DataParallel(model)
        optimizer = checkpoint['optimizer']

    logger = get_logger()

    # Move to GPU, if available
    model = model.to(device)

    # Custom dataloaders
    train_dataset = DIMDataset('train')
    train_loader = torch.utils.data.DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True,
                                               num_workers=num_workers)
    # valid_dataset = DIMDataset('valid')
    # valid_loader = torch.utils.data.DataLoader(valid_dataset, batch_size=args.batch_size, shuffle=False,
    #                                            num_workers=num_workers)

    # scheduler = MultiStepLR(optimizer, milestones=[10, 20], gamma=0.1)

    # Epochs
    for epoch in range(start_epoch, args.end_epoch):
        # scheduler.step(epoch)

        # One epoch's training
        train_loss = train(train_loader=train_loader,
                           model=model,
                           optimizer=optimizer,
                           epoch=epoch,
                           logger=logger)
        effective_lr = get_learning_rate(optimizer)
        print('Current effective learning rate: {}\n'.format(effective_lr))

        writer.add_scalar('model/train_loss', train_loss, epoch)

        # One epoch's validation
        # valid_loss = valid(valid_loader=valid_loader,
        #                    model=model,
        #                    logger=logger)
        #
        # writer.add_scalar('Valid_Loss', valid_loss, epoch)

        # One epoch's test
        sad_loss, mse_loss = test(model)
        writer.add_scalar('model/sad_loss', sad_loss, epoch)
        writer.add_scalar('model/mse_loss', mse_loss, epoch)

        # Print status
        status = 'Test: SAD {:.4f} MSE {:.4f}\n'.format(sad_loss, mse_loss)
        logger.info(status)

        # Check if there was an improvement
        is_best = mse_loss < best_loss
        best_loss = min(mse_loss, best_loss)
        if not is_best:
            epochs_since_improvement += 1
            print("\nEpochs since last improvement: %d\n" % (epochs_since_improvement,))
        else:
            epochs_since_improvement = 0

        # Save checkpoint
        save_checkpoint(epoch, epochs_since_improvement, model, optimizer, best_loss, is_best)


def train(train_loader, model, optimizer, epoch, logger):
    model.train()  # train mode (dropout and batchnorm is used)

    losses = AverageMeter()

    # Batches
    for i, (img, alpha_label) in enumerate(train_loader):
        # Move to GPU, if available
        img = img.type(torch.FloatTensor).to(device)  # [N, 4, 320, 320]
        alpha_label = alpha_label.type(torch.FloatTensor).to(device)  # [N, 2, 320, 320]
        alpha_label = alpha_label.reshape((-1, 2, im_size * im_size))  # [N, 2, 320*320]

        # Forward prop.
        alpha_out = model(img)  # [N, 320, 320]
        alpha_out = alpha_out.reshape((-1, 1, im_size * im_size))  # [N, 320*320]

        # Calculate loss
        # loss = criterion(alpha_out, alpha_label)
        loss = alpha_prediction_loss(alpha_out, alpha_label)

        # Back prop.
        optimizer.zero_grad()
        loss.backward()

        # Clip gradients
        clip_gradient(optimizer, grad_clip)

        # Update weights
        optimizer.step()

        # Keep track of metrics
        losses.update(loss.item())

        # Print status

        if i % print_freq == 0:
            status = 'Epoch: [{0}][{1}/{2}]\t' \
                     'Loss {loss.val:.4f} ({loss.avg:.4f})\t'.format(epoch, i, len(train_loader), loss=losses)
            logger.info(status)

    return losses.avg


def valid(valid_loader, model, logger):
    model.eval()  # eval mode (dropout and batchnorm is NOT used)

    losses = AverageMeter()

    # Batches
    for img, alpha_label in tqdm(valid_loader):
        # Move to GPU, if available
        img = img.type(torch.FloatTensor).to(device)  # [N, 4, 320, 320]
        alpha_label = alpha_label.type(torch.FloatTensor).to(device)  # [N, 2, 320, 320]
        alpha_label = alpha_label.reshape((-1, 2, im_size * im_size))  # [N, 2, 320*320]

        # Forward prop.
        alpha_out = model(img)  # [N, 320, 320]
        alpha_out = alpha_out.reshape((-1, 1, im_size * im_size))  # [N, 320*320]

        # Calculate loss
        # loss = criterion(alpha_out, alpha_label)
        loss = alpha_prediction_loss(alpha_out, alpha_label)

        # Keep track of metrics
        losses.update(loss.item())

    # Print status
    status = 'Validation: Loss {loss.avg:.4f}\n'.format(loss=losses)
    logger.info(status)

    return losses.avg


def main():
    global args
    args = parse_args()
    train_net(args)


if __name__ == '__main__':
    main()
