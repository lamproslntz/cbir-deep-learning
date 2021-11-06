import os
import sys
import logging
import joblib
from PIL import Image
import numpy as np
import torch
import torchvision.transforms as transforms
from torch.utils.data import DataLoader
from models.image_dataset import ImageDataset
from models import pretrained_models
from models.utils import predict, label_to_vector
# from flask import Flask

# logger config
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s [%(name)s] : %(message)s')
logger = logging.getLogger(__name__)

# app = Flask(__name__)
# @app.route('/')
# def hello_world():  # put application's code here
#     return 'Hello World!'

hook_features = []


def create_docs(directory, model, pca, transform, mapping):
    """ Read CIFAR-10 train data and create Elasticsearch indexable documents.

    The image documents structure is the following: ("id", "filename", "path", "features").
    The "features" field refers to the image feature vector which consists of:
        * the image embeddings found by the deep-learning model and then reduced using PCA,
        * the one-hot class label vector.

    Args:
        directory:
            CIFAR-10 train data directory, as string.
        model:
            deep-learning model, as Pytorch object.
        pca:
           Principal Component Analysis (PCA), as scikit-learn model.
        transform:
            image transformations, as Pytorch object.
        mapping:
            CIFAR-10 label to index mapping, as dictionary.

    Returns:
        images (documents), as list of dictionaries.
        number of total features, as integer.
    """
    if not os.path.isdir(directory):
        logger.error(f"Provided path doesn't exist or isn't a directory ...")
        return None, 0
    elif model is None:
        logger.error(f"Provided deep-learning model is None ...")
        return None, 0
    elif pca is None:
        logger.error(f"Provided PCA model is None ...")
        return None, 0

    data = []
    num_features = 0
    for file in os.listdir(directory):
        path = os.path.join(directory, file)

        # create dataset and dataloader objects for Pytorch
        dataset = None
        dataloader = None
        with Image.open(path) as image:
            dataset = ImageDataset([image], transform)
            dataloader = DataLoader(dataset, batch_size=64, shuffle=False)

        # pass image trough deep-learning model to gain the image embedding vector
        predict(dataloader, model, device)
        # extract the image embeddings vector
        embedding = hook_features
        # reduce the dimensionality of the embedding vector
        embedding = pca.transform(embedding)

        # get image class label as one-hot vector
        label_str = file[file.find('-') + 1: file.find('.')]
        label_vec = label_to_vector(label_str, mapping)

        # concatenate embeddings and label vector
        features_vec = np.concatenate((embedding, label_vec), axis=None)
        num_features = features_vec.shape[0]  # total number of image features

        doc = {
            'id': file[0: file.find('-')],
            'filename': file,
            'path': path,
            'features': features_vec
        }
        data.append(doc)

    return data, num_features


def get_features():
    """ Hook for extracting image embeddings from the layer that is attached to.

    Returns:
        hook, as callable.
    """
    def hook(model, input, output):
        global hook_features
        hook_features = output.detach().cpu().numpy()
    return hook


if __name__ == '__main__':
    # path for VGG-16 and PCA models
    path_vgg_16 = 'saved-model/vgg16-weights.pth'
    path_pca = 'saved-model/pca.joblib'

    # path for CIFAR-10 train and test datasets
    dir_train = 'static/cifar10/train'
    dir_test = 'static/cifar10/test'

    # CIFAR-10 labels to numbers
    label_mapping = {
        'airplane': 0,
        'automobile': 1,
        'bird': 2,
        'cat': 3,
        'deer': 4,
        'dog': 5,
        'frog': 6,
        'horse': 7,
        'ship': 8,
        'truck': 9
    }

    # get available device (CPU/GPU)
    device = torch.device(f"cuda" if torch.cuda.is_available() else "cpu")
    logger.info(f'Using {device} device ...')

    logger.info(f'Loading VGG-16 model from {path_vgg_16} ...')
    # initialize VGG-16
    model = pretrained_models.initialize_model(pretrained=True,
                                               num_labels=len(label_mapping),
                                               feature_extracting=True)
    # load VGG-16 pretrained weights
    model.load_state_dict(torch.load(path_vgg_16, map_location='cuda:0'))
    # send VGG-16 to CPU/GPU
    model.to(device)
    # register hook
    model.classifier[5].register_forward_hook(get_features())

    logger.info(f'Loading PCA model from {path_pca} ...')
    # load PCA pretrained model
    pca = joblib.load(path_pca)

    # image transformations
    transform = transforms.Compose([
        transforms.Resize(224),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
    ])

    logger.info("Loading CIFAR-10 data and creating Elasticsearch documents ...")
    images, num_features = create_docs(dir_train, model, pca, transform, label_mapping)
    if (images is None) or (num_features == 0):
        logger.error("Number of Elasticsearch documents is 0 ...")
        sys.exit(1)

    # Elasticsearch config
    INDEX_NAME = 'cifar10'

    # app.run()